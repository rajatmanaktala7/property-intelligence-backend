
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, text

VERSION = "1.9.21-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"
MODE = "NATURAL_COMMERCIAL_ATOMIC_SPLIT_1_9T2"

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
    "INVENTORY_GROUP",
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

MIGRATIONS = [
    "ALTER TABLE alliance_gold_spans ADD COLUMN IF NOT EXISTS parent_span_id UUID",
    "ALTER TABLE alliance_gold_spans ADD COLUMN IF NOT EXISTS span_status TEXT NOT NULL DEFAULT 'ACTIVE'",
    "ALTER TABLE alliance_gold_spans ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ",
    "ALTER TABLE alliance_gold_spans ADD COLUMN IF NOT EXISTS superseded_by JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE alliance_gold_spans ADD COLUMN IF NOT EXISTS lineage_metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
    "CREATE INDEX IF NOT EXISTS idx_gold_spans_active_status ON alliance_gold_spans(span_status, boundary_status)",
    "CREATE INDEX IF NOT EXISTS idx_gold_spans_parent ON alliance_gold_spans(parent_span_id)",
]

def install(engine) -> Dict[str, Any]:
    with engine.begin() as conn:
        for statement in [x.strip() for x in DDL.split(";") if x.strip()]:
            conn.execute(text(statement))
        for statement in MIGRATIONS:
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

def _clean_anchor_text(line: str) -> str:
    s = (line or "").strip()
    # Remove any leading emoji / bullet / punctuation until the first
    # alphanumeric character. This covers 💰 Rent, 🅿️ Parking, 🔑 Possession,
    # as well as decorative project bullets such as ✨ DLH LEGACY.
    s = re.sub(r"^[^A-Za-z0-9]+", "", s)
    return s.strip()

def _is_dependent_attribute_line(line: str) -> bool:
    s = _clean_anchor_text(line)
    return bool(re.match(
        r"^(?:RENT|ASKING|DEMAND|PRICE|DEPOSIT|SECURITY|CAM|MAINTENANCE|"
        r"POSSESSION|AVAILABLE|AVAILABILITY|PARKING|CAR\s+PARKING|"
        r"FURNISHED|SEMI[\-\s]?FURNISHED|FULLY\s+FURNISHED|"
        r"LOWER\s+FLOOR|UPPER\s+FLOOR|GROUND\s+FLOOR|FIRST\s+FLOOR|"
        r"NOTICE|KITCHEN|AC\b)",
        s,
        re.I,
    ))

def _is_named_property_anchor(line: str) -> bool:
    """
    Detect short project/building/property heading lines such as:
    '✨ DLH LEGACY', 'PARK GRANDEUR', 'RUSTOMJEE PARAMOUNT – KHAR WEST'.

    Deliberately avoids using rent/price/parking/possession as boundaries.
    """
    s = _clean_anchor_text(line)
    if not s or len(s) < 3 or len(s) > 90:
        return False
    if _is_dependent_attribute_line(s):
        return False
    if PHONE_RE.search(s):
        return False
    if AREA_RE.search(s) or MONEY_RE.search(s):
        return False
    if re.search(r"\b(?:BHK|SQFT|SQ\s*FT|SFT|YARDS?|ACRE|RENT|SALE|DEMAND|ASKING)\b", s, re.I):
        return False

    # Mostly title-like text. Allow location suffixes and digits in project names.
    letters = [ch for ch in s if ch.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)

    # Project headings in the imported feeds are usually uppercase.
    return upper_ratio >= 0.75 and len(s.split()) <= 12

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

        # Dependent commercial/property attributes stay with the entity above.
        if i > 0 and _is_dependent_attribute_line(s):
            continue

        # A short project/building heading starts a new span when the preceding
        # block already contains property facts and the following lines contain
        # property facts. This handles broker inventory dumps such as:
        # DLH LEGACY ... Rent ... / DLH LEGACY ... Rent ...
        if i > 0 and _is_named_property_anchor(s):
            prev_window = "\n".join(x[2] for x in lines[max(0, i - 8):i])
            next_window = "\n".join(x[2] for x in lines[i:min(len(lines), i + 6)])
            prev_has_property_fact = bool(PROPERTY_FACT_RE.search(prev_window))
            next_has_property_fact = bool(PROPERTY_FACT_RE.search(next_window))
            if prev_has_property_fact and next_has_property_fact:
                starts.append(start)
                continue

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

AREA_FLEX_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>"
    r"SQ\.?\s*FT\.?|SQFT|SFT|SQ\.?\s*YD\.?|SQYD|SYD|SYDS|GAJ|YARDS?|"
    r"SQ\.?\s*M\.?|SQM|SQ\.?\s*MT\.?|SQMT|ACRE|ACRES)\b",
    re.I,
)


def _normalized_area_unit(unit: str) -> str:
    u = re.sub(r"[\s.]+", "", (unit or "").upper())
    aliases = {
        "SQFT": "SQFT", "SFT": "SQFT",
        "SQYD": "SQYD", "SYD": "SQYD", "SYDS": "SQYD",
        "GAJ": "SQYD", "YARD": "SQYD", "YARDS": "SQYD",
        "SQM": "SQM", "SQMT": "SQM",
        "ACRE": "ACRE", "ACRES": "ACRE",
    }
    return aliases.get(u, u)


def _money_unit(unit: str) -> str:
    u = (unit or "").upper()
    if u.startswith("CR"):
        return "CRORE"
    if u in {"L", "LAC", "LACS", "LAKH", "LAKHS"}:
        return "LAKH"
    return u


def _money_normalized_inr(value: float, unit: str) -> Optional[float]:
    u = _money_unit(unit)
    if u == "CRORE":
        return value * 10000000
    if u == "LAKH":
        return value * 100000
    return None


def _line_for_offset(text_value: str, offset: int) -> str:
    start = text_value.rfind("\n", 0, offset) + 1
    end = text_value.find("\n", offset)
    if end < 0:
        end = len(text_value)
    return text_value[start:end].strip()


def _title_parts(span_text: str) -> Dict[str, Optional[str]]:
    # Foundation 1.9O2 source-grounded title parser.
    #
    # A map-pin heading is explicit source evidence that the first line is a
    # project/location heading. Mixed-case names such as "Supriya Sec- 10"
    # must not be rejected by the older uppercase-only generic anchor rule.
    lines = [x.strip() for x in (span_text or "").splitlines() if x.strip()]
    if not lines:
        return {"project_name": None, "locality": None}

    raw_title = lines[0]
    is_pin_heading = bool(re.match(r"^\s*📍", raw_title))

    # Preserve the conservative generic detector for normal text. Pin-heading
    # children produced by Foundation 1.9M are allowed through directly.
    if not is_pin_heading and not _is_named_property_anchor(raw_title):
        return {"project_name": None, "locality": None}

    title = _clean_anchor_text(raw_title)
    if not title:
        return {"project_name": None, "locality": None}

    # Compact project + sector headings.
    sector_match = re.match(
        r"^(?P<project>.+?)\s+"
        r"(?P<label>SECTOR|SEC)\s*[-:]?\s*"
        r"(?P<number>\d+(?:\s*/\s*[A-Z0-9]+)?)\s*$",
        title,
        re.I,
    )
    if sector_match:
        project = re.sub(r"\s+", " ", sector_match.group("project")).strip(" -–—")
        number = re.sub(r"\s+", "", sector_match.group("number")).strip()
        locality = f"Sector {number}" if number else None
        return {"project_name": project or None, "locality": locality}

    # Explicit project/locality separator.
    parts = re.split(r"\s+[–—-]\s+", title, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        locality_signal = bool(
            re.search(
                r"\b(?:WEST|EAST|NORTH|SOUTH|CENTRAL|SECTOR|SEC|PHASE|"
                r"EXTENSION|EXTN|EXPRESSWAY|EXPWY|ROAD|MARG)\b",
                right,
                re.I,
            )
        )
        if locality_signal:
            sec = re.fullmatch(
                r"(?:SECTOR|SEC)\s*[-:]?\s*(\d+(?:\s*/\s*[A-Z0-9]+)?)",
                right,
                re.I,
            )
            if sec:
                number = re.sub(r"\s+", "", sec.group(1))
                right = f"Sector {number}"
            return {"project_name": left or None, "locality": right or None}

    return {"project_name": title or None, "locality": None}



def _extract_property_fields(span_text: str, tx: str) -> Dict[str, Any]:
    s = span_text or ""
    fields: Dict[str, Any] = {}

    config = re.search(r"\b(\d+(?:\.\d+)?)\s*BHK\b", s, re.I)
    if config:
        value = config.group(1)
        if value.endswith(".0"):
            value = value[:-2]
        fields["configuration"] = f"{value} BHK"

    if re.search(r"\bFULLY[\s-]*FURNISHED\b", s, re.I):
        fields["furnishing"] = "FULLY_FURNISHED"
    elif re.search(r"\bSEMI[\s-]*FURNISHED\b", s, re.I):
        fields["furnishing"] = "SEMI_FURNISHED"
    elif re.search(r"\bUNFURNISHED\b|\bBARE[\s-]*SHELL\b", s, re.I):
        fields["furnishing"] = "UNFURNISHED"

    if re.search(r"\bNO\s+CAR\s+PARKING\b|\bNO\s+PARKING\b", s, re.I):
        fields["parking_count"] = 0
    else:
        parking = re.search(r"\b(\d+)\s+CAR\s+PARKINGS?\b", s, re.I)
        if parking:
            fields["parking_count"] = int(parking.group(1))

    floor = re.search(
        r"\b(LOWER\s+FLOOR|UPPER\s+FLOOR|GROUND\s+FLOOR|FIRST\s+FLOOR|"
        r"SECOND\s+FLOOR|THIRD\s+FLOOR|HIGHER\s+FLOOR|MIDDLE\s+FLOOR)\b",
        s,
        re.I,
    )
    if floor:
        fields["floor_description"] = floor.group(1).title()

    view = re.search(
        r"\b(GARDEN\s+FACING|SEA\s+FACING|PARK\s+FACING|ROAD\s+FACING|"
        r"POOL\s+FACING|GREEN\s+FACING)\b",
        s,
        re.I,
    )
    if view:
        fields["view"] = view.group(1).title()

    for line in s.splitlines():
        clean = _clean_anchor_text(line)
        if re.search(r"\bKITCHEN\b", clean, re.I):
            fields["kitchen_features"] = clean.strip()
            break

    if re.search(r"\bNEGOTIABLE\b", s, re.I):
        fields["negotiable"] = True

    if re.search(r"\bIMMEDIATE\s+POSSESSION\b", s, re.I):
        fields["possession"] = "Immediate"
    elif re.search(r"\bREADY\s+TO\s+MOVE\b", s, re.I):
        fields["possession"] = "Ready to Move"

    notice = re.search(
        r"\b((?:ONE|TWO|THREE|\d+)\s+DAY[S]?\s+NOTICE(?:\s+WITH\s+PROFILE)?)\b",
        s,
        re.I,
    )
    if notice:
        fields["inspection_notice"] = " ".join(word.capitalize() for word in notice.group(1).split())

    if re.search(r"\bRENT\s*:\s*ON\s+REQUEST\b|\bRENT\s+ON\s+REQUEST\b", s, re.I):
        fields["rent_on_request"] = True
    if re.search(r"\bPRICE\s*:\s*ON\s+REQUEST\b|\bPRICE\s+ON\s+REQUEST\b", s, re.I):
        fields["price_on_request"] = True

    deposit_months = re.search(
        r"\b(?:DEPOSIT|SECURITY(?:\s+DEPOSIT)?)\s*:\s*(\d+(?:\.\d+)?)\s*MONTHS?\b",
        s,
        re.I,
    )
    if deposit_months:
        value = float(deposit_months.group(1))
        fields["security_deposit_months"] = int(value) if value.is_integer() else value

    return fields


def _extract_requirement_fields(span_text: str, tx: str) -> Dict[str, Any]:
    s = span_text or ""
    if not (DEMAND_RE.search(s) and not AVAILABILITY_RE.search(s)):
        return {}

    fields: Dict[str, Any] = {}
    config = re.search(r"\b(\d+(?:\.\d+)?)\s*BHK\b", s, re.I)
    if config:
        fields["configuration"] = f"{config.group(1)} BHK"

    use = re.search(
        r"\b(RESTAURANT|CAFE|BANQUET|CLINIC|HOSPITAL|SHOWROOM|SHOP|OFFICE|"
        r"WAREHOUSE|GODOWN|HOTEL|GUEST\s+HOUSE|RETAIL)\b",
        s,
        re.I,
    )
    if use:
        fields["intended_use"] = use.group(1).upper().replace(" ", "_")

    fields["transaction_type"] = tx
    return fields


def propose_fields(span_text: str) -> Dict[str, Any]:
    """
    Foundation 1.3 source-grounded proposal engine.
    Reviewer assistance only. Human judgment remains ground truth.
    """
    s = span_text or ""

    if DEMAND_RE.search(s) and not AVAILABILITY_RE.search(s):
        content_hint = "REQUIREMENT"
    elif AVAILABILITY_RE.search(s) or PROPERTY_FACT_RE.search(s):
        content_hint = "PROPERTY_AVAILABILITY"
    else:
        content_hint = "FRAGMENT"

    has_sale = bool(re.search(r"\bFOR\s+SALE\b|\bSALE\b", s, re.I))
    has_rent = bool(re.search(r"\bFOR\s+RENT\b|\bRENT(?:AL)?\b", s, re.I))
    if has_sale and has_rent:
        tx = "BOTH"
    elif has_rent:
        tx = "RENT"
    elif has_sale:
        tx = "SALE"
    else:
        tx = "UNKNOWN"

    title = _title_parts(s)

    areas = []
    for m in AREA_FLEX_RE.finditer(s):
        raw_value = float(m.group("value"))
        areas.append({
            "value": int(raw_value) if raw_value.is_integer() else raw_value,
            "unit": _normalized_area_unit(m.group("unit")),
            "role": "UNKNOWN",
            "evidence": m.group(0),
        })

    money = []
    for m in MONEY_RE.finditer(s):
        raw_value = float(m.group("value"))
        unit = _money_unit(m.group("unit"))
        line = _line_for_offset(s, m.start())

        if re.search(r"\bRENT\b", line, re.I):
            role = "TOTAL_RENT"
        elif re.search(r"\bDEPOSIT\b|\bSECURITY\b", line, re.I):
            role = "SECURITY_DEPOSIT"
        elif tx == "SALE" and re.search(r"\bPRICE\b|\bASKING\b|\bDEMAND\b|\bSALE\b", line, re.I):
            role = "TOTAL_SALE_PRICE"
        else:
            role = "AMBIGUOUS"

        value = int(raw_value) if raw_value.is_integer() else raw_value
        item = {
            "value": value,
            "unit": unit,
            "role": role,
            "evidence": m.group(0),
        }
        normalized = _money_normalized_inr(raw_value, unit)
        if normalized is not None:
            item["normalized_inr"] = int(normalized) if float(normalized).is_integer() else normalized
        if role == "TOTAL_RENT" and re.search(r"\bNEGOTIABLE\b", line, re.I):
            item["negotiable"] = True
        money.append(item)

    for dm in re.finditer(
        r"\b(?:DEPOSIT|SECURITY(?:\s+DEPOSIT)?)\s*:\s*(\d+(?:\.\d+)?)\s*MONTHS?\b",
        s,
        re.I,
    ):
        value = float(dm.group(1))
        money.append({
            "value": int(value) if value.is_integer() else value,
            "unit": "MONTHS",
            "role": "SECURITY_DEPOSIT",
            "evidence": dm.group(0),
        })

    contacts = [{"phone": m.group(0), "role": "UNKNOWN"} for m in PHONE_RE.finditer(s)]

    property_fields = _extract_property_fields(s, tx) if content_hint == "PROPERTY_AVAILABILITY" else {}
    requirement_fields = _extract_requirement_fields(s, tx) if content_hint == "REQUIREMENT" else {}

    suitable_uses = []
    use_match = re.search(
        r"\b(RESTAURANT|CAFE|BANQUET|CLINIC|HOSPITAL|SHOWROOM|SHOP|OFFICE|"
        r"WAREHOUSE|GODOWN|HOTEL|GUEST\s+HOUSE|RETAIL)\s+(?:SUITABLE|ALLOWED)\b",
        s,
        re.I,
    )
    if use_match:
        suitable_uses.append(use_match.group(1).upper().replace(" ", "_"))

    return {
        "content_type_hint": content_hint,
        "transaction_type_hint": tx,
        "project_name_hint": title.get("project_name"),
        "city_hint": None,
        "locality_hint": title.get("locality"),
        "unit_identifier_hint": None,
        "acceptable_locations": [],
        "suitable_uses": suitable_uses,
        "areas": areas,
        "money_mentions": money,
        "contacts": contacts,
        "property_fields": property_fields,
        "requirement_fields": requirement_fields,
        "field_confidence": {
            "principle": "SOURCE_SUPPORTED_ONLY",
            "human_review_required": True,
        },
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
            source_row_ref, source_meta = _v19a_source_metadata(
                engine,
                table_name,
                column_name,
                raw,
            )
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
                        :source_message_id, :source_table, :source_row_ref,
                        :source_fingerprint, :raw_text,
                        CAST(:source_metadata AS jsonb),
                        :sampling_bucket, :message_length, :proposed_span_count
                    )
                    """
                ),
                {
                    "source_message_id": source_id,
                    "source_table": table_name,
                    "source_row_ref": source_row_ref,
                    "source_fingerprint": fp,
                    "raw_text": raw,
                    "source_metadata": _json(source_meta),
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


def repropose_unlabeled_gold(engine) -> Dict[str, Any]:
    """
    Rebuild proposals in Academy tables only.
    Hard-blocked if any human Gold labels or relationship labels exist.
    """
    with engine.begin() as conn:
        label_count = int(conn.execute(
            text("SELECT count(*) FROM alliance_gold_span_labels WHERE active=TRUE")
        ).scalar() or 0)
        relationship_count = int(conn.execute(
            text("SELECT count(*) FROM alliance_gold_relationship_labels WHERE active=TRUE")
        ).scalar() or 0)

        if label_count or relationship_count:
            raise HTTPException(
                409,
                "Reproposal blocked because human Gold labels/relationships already exist."
            )

        sources = conn.execute(text(
            """
            SELECT source_message_id, raw_text
            FROM alliance_gold_source_messages
            ORDER BY created_at, source_message_id
            """
        )).mappings().all()

        old_count = int(conn.execute(
            text("SELECT count(*) FROM alliance_gold_spans")
        ).scalar() or 0)

        conn.execute(text("DELETE FROM alliance_gold_spans"))

        new_count = 0
        for source in sources:
            source_id = str(source["source_message_id"])
            raw = str(source["raw_text"] or "")
            spans = propose_spans(raw)

            conn.execute(
                text(
                    """
                    UPDATE alliance_gold_source_messages
                    SET proposed_span_count=:n,
                        labeling_status='UNLABELED',
                        updated_at=now()
                    WHERE source_message_id=:sid
                    """
                ),
                {"n": len(spans), "sid": source_id},
            )

            for span in spans:
                conn.execute(
                    text(
                        """
                        INSERT INTO alliance_gold_spans (
                            span_id, source_message_id, span_order,
                            proposed_start_offset, proposed_end_offset,
                            proposed_text, proposal_method,
                            proposal_confidence, boundary_status
                        )
                        VALUES (
                            :span_id, :sid, :span_order,
                            :start_offset, :end_offset,
                            :proposed_text, 'DETERMINISTIC_V1_2_2',
                            :proposal_confidence, 'PENDING'
                        )
                        """
                    ),
                    {
                        "span_id": str(uuid.uuid4()),
                        "sid": source_id,
                        "span_order": span["span_order"],
                        "start_offset": span["start_offset"],
                        "end_offset": span["end_offset"],
                        "proposed_text": span["text"],
                        "proposal_confidence": span["proposal_confidence"],
                    },
                )
                new_count += 1

    return {
        "status": "REPROPOSED",
        "version": VERSION,
        "source_messages_preserved": len(sources),
        "old_proposed_spans": old_count,
        "new_proposed_spans": new_count,
        "human_labels_present": 0,
        "relationship_labels_present": 0,
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

    boundary_action = str(payload.get("boundary_action") or "CORRECT").upper()
    if boundary_action in {"SPLIT", "MERGE"}:
        raise HTTPException(
            409,
            "Foundation 1.4 uses real atomic boundary operations. "
            "Use the Atomic Split or Merge endpoint instead of saving a cosmetic SPLIT/MERGE label."
        )

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
# Foundation 1.4: real atomic span editing + lineage
# ---------------------------------------------------------------------------

ATOMIC_PROPERTY_HEADING_RE = re.compile(
    r"\b(?:BUILDER\s+FLOOR|INDEPENDENT\s+FLOOR|FLAT|APARTMENT|VILLA|OFFICE|"
    r"SHOP|SHOWROOM|PROPERTY|WAREHOUSE|GODOWN|PLOT|LAND)\b.*"
    r"\b(?:AVAILABLE\s+FOR\s+(?:LEASE|RENT|SALE)|FOR\s+(?:LEASE|RENT|SALE))\b",
    re.I,
)
ATOMIC_CONTEXT_START_RE = re.compile(
    r"^(?:FOR\s+MORE\s+DETAILS\s+CONTACT(?:\s+US)?|PICTURES?\s+ON\s+CALL|"
    r"FOR\s+SITE\s+VISITS?|CONTACT\s+OUR\s+TEAM|BROKER\s+DETAILS?)\b",
    re.I,
)

def _boundary_clean_line(value: str) -> str:
    s = html.unescape(str(value or "")).strip()
    s = re.sub(r"^[^A-Za-z0-9]+", "", s)
    s = re.sub(r"[^A-Za-z0-9)]+$", "", s)
    return re.sub(r"\s+", " ", s).strip()

def _line_ranges(raw: str) -> List[Tuple[int, int, str]]:
    out: List[Tuple[int, int, str]] = []
    pos = 0
    for line in (raw or "").splitlines(keepends=True):
        start = pos
        pos += len(line)
        out.append((start, pos, line))
    if pos < len(raw or ""):
        out.append((pos, len(raw), raw[pos:]))
    return out

def _trim_atomic_block(raw: str, start: int, end: int) -> Tuple[int, int]:
    block = raw[start:end]
    lines = _line_ranges(block)
    cut = len(block)
    for i, (ls, _le, line) in enumerate(lines):
        if i == 0:
            continue
        clean = _boundary_clean_line(line)
        if ATOMIC_CONTEXT_START_RE.search(clean):
            cut = ls
            break
    trimmed = block[:cut]
    leading = len(trimmed) - len(trimmed.lstrip())
    trailing_text = trimmed.rstrip()
    return start + leading, start + len(trailing_text)

def _context_from_ranges(raw: str, ranges: List[Tuple[int, int]]) -> List[str]:
    if not ranges:
        return [raw.strip()] if (raw or "").strip() else []
    snippets: List[str] = []
    cursor = 0
    for start, end in sorted(ranges):
        if start > cursor:
            gap = raw[cursor:start].strip()
            if gap:
                snippets.append(gap)
        cursor = max(cursor, end)
    if cursor < len(raw):
        gap = raw[cursor:].strip()
        if gap:
            snippets.append(gap)
    return snippets



# ---------------------------------------------------------------------------
# Foundation 1.8D: retroactive shared source contact provenance
# ---------------------------------------------------------------------------

V18_FOOTER_SIGNAL_RE = re.compile(
    r"\b(?:FOR\s+MORE\s+DETAILS|SITE\s+VISITS?|CONTACT|CALL|DM|QUERY)\b",
    re.I,
)

V18_FOOTER_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?91[\s\-]*)?[6-9](?:[\s\-]*\d){9}(?!\d)"
)

def _v18_normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    if len(digits) == 10:
        return "+91" + digits
    return str(value or "").strip()

def _v18_shared_source_contacts(source_raw: str) -> List[Dict[str, Any]]:
    raw = str(source_raw or "")
    footer_start = None

    for start, _end, line in _line_ranges(raw):
        if V18_FOOTER_SIGNAL_RE.search(_boundary_clean_line(line)):
            footer_start = start
            break

    if footer_start is None:
        return []

    footer = raw[footer_start:].strip()
    phone_matches = list(V18_FOOTER_PHONE_RE.finditer(footer))
    if not phone_matches:
        return []

    footer_lines = [re.sub(r"\s+", " ", x).strip() for x in footer.splitlines()]
    footer_lines = [x for x in footer_lines if x]

    result: List[Dict[str, Any]] = []
    seen = set()

    for match in phone_matches:
        phone = _v18_normalize_phone(match.group(0))
        if not phone or phone in seen:
            continue
        seen.add(phone)

        phone_digits = re.sub(r"\D", "", match.group(0))
        phone_line_index = None
        for idx, line in enumerate(footer_lines):
            if phone_digits and phone_digits in re.sub(r"\D", "", line):
                phone_line_index = idx
                break

        name = None
        company = None
        if phone_line_index is not None:
            candidates: List[str] = []
            for line in footer_lines[max(0, phone_line_index - 4):phone_line_index]:
                if V18_FOOTER_SIGNAL_RE.search(line):
                    continue
                if V18_FOOTER_PHONE_RE.search(line):
                    continue
                candidates.append(line)

            if len(candidates) >= 2:
                name = candidates[-2]
                company = candidates[-1]
            elif len(candidates) == 1:
                company = candidates[-1]

        result.append({
            "phone": phone,
            "name": name,
            "company": company,
            "role": "SOURCE_CONTACT",
            "provenance": "MESSAGE_FOOTER",
            "scope": "SHARED_SOURCE_MESSAGE",
            "owner_status": "NOT_PROVEN",
            "broker_status": "SOURCE_OR_BROKER_CONTEXT",
            "evidence": footer,
        })

    return result

def _v18_merge_source_contacts(
    proposal: Dict[str, Any],
    source_raw: str,
) -> Dict[str, Any]:
    p = dict(proposal or {})
    shared = _v18_shared_source_contacts(source_raw)
    if not shared:
        return p

    existing = list(p.get("contacts") or [])
    known = {
        re.sub(r"\D", "", str(x.get("phone") or ""))
        for x in existing
        if isinstance(x, dict)
    }

    for item in shared:
        digits = re.sub(r"\D", "", str(item.get("phone") or ""))
        if digits and digits not in known:
            existing.append(dict(item))
            known.add(digits)

    p["contacts"] = existing
    p["shared_source_contact_provenance"] = "MESSAGE_FOOTER"
    p["shared_contact_is_owner"] = False
    p["shared_source_contact_recovered_from_original_message"] = True
    return p



# ---------------------------------------------------------------------------
# Foundation 1.9: WhatsApp sender metadata contact fallback
# ---------------------------------------------------------------------------

V19_WHATSAPP_SOURCE_RE = re.compile(r"(?:WHATSAPP|WA_)", re.I)

V19_SENDER_PHONE_EXACT = {
    "sender", "sender_phone", "sender_number", "sender_mobile", "sender_msisdn",
    "from_number", "from_phone", "from_mobile", "author", "author_phone",
    "author_number", "participant", "participant_phone", "participant_number",
    "wa_id", "whatsapp_number", "whatsapp_phone",
}

V19_SENDER_NAME_EXACT = {
    "sender_name", "author_name", "participant_name", "push_name",
    "sender_display_name", "contact_name",
}

def _v19_phone_from_sender_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    digits = re.sub(r"\D", "", raw)

    if len(digits) >= 12 and digits.startswith("91"):
        candidate = digits[:12]
        if candidate[2] in "6789":
            return "+" + candidate

    if len(digits) >= 10:
        candidate = digits[-10:]
        if candidate[0] in "6789":
            return "+91" + candidate

    return None

def _v19_sender_phone_columns(column_names: List[str]) -> List[str]:
    out: List[str] = []
    for name in column_names:
        low = str(name or "").lower()
        if low in V19_SENDER_PHONE_EXACT:
            out.append(name)
            continue

        sender_semantic = bool(
            re.search(r"(?:^|_)(sender|from|author|participant)(?:_|$)", low)
        )
        phone_semantic = bool(
            re.search(r"(phone|number|mobile|msisdn|jid|id)$", low)
        )
        if sender_semantic and phone_semantic:
            out.append(name)

    return out

def _v19_sender_name_columns(column_names: List[str]) -> List[str]:
    out: List[str] = []
    for name in column_names:
        low = str(name or "").lower()

        if low in V19_SENDER_NAME_EXACT:
            out.append(name)
            continue

        sender_semantic = bool(
            re.search(r"(?:^|_)(sender|author|participant)(?:_|$)", low)
        )
        name_semantic = bool(re.search(r"(name|display)", low))

        if sender_semantic and name_semantic:
            out.append(name)

    return out

def _v19_whatsapp_sender_contact(
    engine,
    source_table: str,
    source_raw_text: str,
    source_metadata: Any,
) -> Optional[Dict[str, Any]]:
    table_name = str(source_table or "").strip()
    raw = str(source_raw_text or "")

    if not table_name or not raw:
        return None

    if not V19_WHATSAPP_SOURCE_RE.search(table_name):
        return None

    try:
        column_info = _columns(engine, table_name)
    except Exception:
        return None

    column_names = [str(c.get("column_name") or "") for c in column_info]
    column_set = set(column_names)

    metadata = _loads(source_metadata, {})
    if not isinstance(metadata, dict):
        metadata = {}

    raw_column = str(metadata.get("source_column") or "").strip()

    if raw_column not in column_set:
        raw_column = next(
            (
                c
                for c in (
                    "raw_message",
                    "raw_text",
                    "message",
                    "message_text",
                    "body",
                    "text",
                )
                if c in column_set
            ),
            "",
        )

    if not raw_column:
        return None

    phone_columns = _v19_sender_phone_columns(column_names)
    if not phone_columns:
        return None

    name_columns = _v19_sender_name_columns(column_names)

    select_columns: List[str] = []
    for col in phone_columns + name_columns:
        if col not in select_columns:
            select_columns.append(col)

    qt = _safe_identifier(table_name)
    qr = _safe_identifier(raw_column)
    qs = ", ".join(_safe_identifier(c) for c in select_columns)

    sql = (
        "SELECT " + qs + " "
        "FROM " + qt + " "
        "WHERE " + qr + "::text = :raw "
        "LIMIT 20"
    )

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(sql),
                {"raw": raw},
            ).mappings().all()
    except Exception:
        return None

    if not rows:
        return None

    phones = set()
    names = set()

    for row in rows:
        for col in phone_columns:
            phone = _v19_phone_from_sender_value(row.get(col))
            if phone:
                phones.add(phone)

        for col in name_columns:
            value = str(row.get(col) or "").strip()
            if value:
                names.add(value)

    # Same text may have been posted by multiple people. Never guess.
    if len(phones) != 1:
        return None

    phone = next(iter(phones))
    sender_name = next(iter(names)) if len(names) == 1 else None

    return {
        "phone": phone,
        "name": sender_name,
        "company": None,
        "role": "SOURCE_CONTACT",
        "provenance": "WHATSAPP_SENDER",
        "scope": "SOURCE_MESSAGE_SENDER",
        "owner_status": "NOT_PROVEN",
        "broker_status": "NOT_PROVEN",
        "source_table": table_name,
        "sender_metadata_columns": phone_columns,
    }

def _v19_merge_sender_fallback(
    engine,
    proposal: Dict[str, Any],
    source_table: str,
    source_raw_text: str,
    source_metadata: Any,
) -> Dict[str, Any]:
    p = dict(proposal or {})

    # Priority:
    # 1. property/body contact
    # 2. shared footer contact
    # 3. WhatsApp sender metadata
    if p.get("contacts"):
        return p

    sender = _v19_whatsapp_sender_contact(
        engine,
        source_table,
        source_raw_text,
        source_metadata,
    )

    if not sender:
        return p

    p["contacts"] = [sender]
    p["sender_contact_fallback_used"] = True
    p["sender_contact_is_owner"] = False
    return p



# ---------------------------------------------------------------------------
# Foundation 1.9A: persistent WhatsApp source-row + sender lineage
# ---------------------------------------------------------------------------

V19A_ROW_ID_PRIORITY = (
    "message_id",
    "whatsapp_message_id",
    "wa_message_id",
    "id",
    "row_id",
    "record_id",
    "uuid",
    "source_id",
    "event_id",
)

def _v19a_identifier_columns(column_names: List[str]) -> List[str]:
    available = [str(c or "") for c in column_names]
    lower_map = {c.lower(): c for c in available}
    out: List[str] = []

    for name in V19A_ROW_ID_PRIORITY:
        if name in lower_map and lower_map[name] not in out:
            out.append(lower_map[name])

    for c in available:
        low = c.lower()
        if c in out:
            continue
        if low.endswith("_id") and not re.search(
            r"(sender|owner|broker|contact|group|chat|user|participant)_id$",
            low,
        ):
            out.append(c)

    return out

def _v19a_metadata_sender_contact(metadata: Any) -> Optional[Dict[str, Any]]:
    meta = _loads(metadata, {})
    if not isinstance(meta, dict):
        return None

    sender = meta.get("whatsapp_sender")
    if not isinstance(sender, dict):
        return None

    phone = _v19_phone_from_sender_value(sender.get("phone"))
    if not phone:
        return None

    return {
        "phone": phone,
        "name": str(sender.get("name") or "").strip() or None,
        "company": None,
        "role": "SOURCE_CONTACT",
        "provenance": "WHATSAPP_SENDER",
        "scope": "SOURCE_MESSAGE_SENDER",
        "owner_status": "NOT_PROVEN",
        "broker_status": "NOT_PROVEN",
        "source_table": meta.get("source_table"),
        "sender_metadata_columns": sender.get("columns") or [],
        "source_row_ref": meta.get("source_row_ref"),
        "lineage_status": meta.get("sender_lineage_status") or "PERSISTED",
    }

def _v19a_resolve_source_lineage(
    engine,
    table_name: str,
    column_name: str,
    raw_text: str,
) -> Dict[str, Any]:
    table_name = str(table_name or "").strip()
    column_name = str(column_name or "").strip()
    raw = str(raw_text or "")

    result: Dict[str, Any] = {
        "source_table": table_name,
        "source_column": column_name,
        "source_row_ref": None,
        "sender_lineage_status": "NOT_APPLICABLE",
        "whatsapp_sender": None,
        "match_count": 0,
    }

    if not table_name or not column_name or not raw:
        result["sender_lineage_status"] = "INSUFFICIENT_INPUT"
        return result

    if not V19_WHATSAPP_SOURCE_RE.search(table_name):
        return result

    try:
        column_info = _columns(engine, table_name)
    except Exception as exc:
        result["sender_lineage_status"] = "SCHEMA_LOOKUP_FAILED"
        result["lineage_error"] = f"{type(exc).__name__}: {exc}"
        return result

    column_names = [str(c.get("column_name") or "") for c in column_info]
    if column_name not in set(column_names):
        result["sender_lineage_status"] = "SOURCE_COLUMN_MISSING"
        return result

    phone_columns = _v19_sender_phone_columns(column_names)
    name_columns = _v19_sender_name_columns(column_names)
    id_columns = _v19a_identifier_columns(column_names)

    select_columns: List[str] = []
    for col in id_columns + phone_columns + name_columns:
        if col not in select_columns:
            select_columns.append(col)

    if select_columns:
        select_sql = ", ".join(_safe_identifier(c) for c in select_columns)
    else:
        select_sql = "1 AS _row_marker"

    qt = _safe_identifier(table_name)
    qc = _safe_identifier(column_name)
    sql = (
        "SELECT " + select_sql + " "
        "FROM " + qt + " "
        "WHERE " + qc + "::text = :raw "
        "LIMIT 3"
    )

    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"raw": raw}).mappings().all()
    except Exception as exc:
        result["sender_lineage_status"] = "SOURCE_LOOKUP_FAILED"
        result["lineage_error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["match_count"] = len(rows)

    if len(rows) == 0:
        result["sender_lineage_status"] = "SOURCE_ROW_NOT_FOUND"
        return result

    if len(rows) != 1:
        result["sender_lineage_status"] = "AMBIGUOUS_SOURCE_ROWS"
        return result

    row = rows[0]

    row_ref = None
    row_ref_column = None
    for col in id_columns:
        value = row.get(col)
        if value is not None and str(value).strip():
            row_ref = str(value).strip()
            row_ref_column = col
            break

    phones = set()
    used_phone_columns = []
    for col in phone_columns:
        phone = _v19_phone_from_sender_value(row.get(col))
        if phone:
            phones.add(phone)
            used_phone_columns.append(col)

    names = set()
    used_name_columns = []
    for col in name_columns:
        value = str(row.get(col) or "").strip()
        if value:
            names.add(value)
            used_name_columns.append(col)

    sender = None
    if len(phones) == 1:
        sender = {
            "phone": next(iter(phones)),
            "name": next(iter(names)) if len(names) == 1 else None,
            "columns": sorted(set(used_phone_columns + used_name_columns)),
            "role": "SOURCE_CONTACT",
            "provenance": "WHATSAPP_SENDER",
            "owner_status": "NOT_PROVEN",
            "broker_status": "NOT_PROVEN",
        }

    result["source_row_ref"] = row_ref
    result["source_row_ref_column"] = row_ref_column
    result["whatsapp_sender"] = sender

    if sender and row_ref:
        result["sender_lineage_status"] = "RESOLVED_UNIQUE_ROW_AND_SENDER"
    elif sender:
        result["sender_lineage_status"] = "RESOLVED_UNIQUE_SENDER_NO_ROW_ID"
    elif row_ref:
        result["sender_lineage_status"] = "RESOLVED_UNIQUE_ROW_NO_SENDER"
    else:
        result["sender_lineage_status"] = "UNIQUE_ROW_NO_USABLE_LINEAGE"

    return result

def _v19a_source_metadata(
    engine,
    table_name: str,
    column_name: str,
    raw_text: str,
) -> Tuple[Optional[str], Dict[str, Any]]:
    lineage = _v19a_resolve_source_lineage(
        engine,
        table_name,
        column_name,
        raw_text,
    )

    metadata: Dict[str, Any] = {
        "source_column": column_name,
        "source_table": table_name,
        "sender_lineage_status": lineage.get("sender_lineage_status"),
        "source_row_ref": lineage.get("source_row_ref"),
        "source_row_ref_column": lineage.get("source_row_ref_column"),
        "source_match_count": lineage.get("match_count"),
    }

    if lineage.get("whatsapp_sender"):
        metadata["whatsapp_sender"] = lineage["whatsapp_sender"]

    if lineage.get("lineage_error"):
        metadata["sender_lineage_error"] = lineage["lineage_error"]

    return lineage.get("source_row_ref"), metadata

def backfill_sender_lineage(engine, dry_run: bool = True) -> Dict[str, Any]:
    with engine.connect() as conn:
        sources = conn.execute(
            text(
                "SELECT source_message_id, source_table, source_row_ref, "
                "raw_text, source_metadata "
                "FROM alliance_gold_source_messages "
                "WHERE source_table IS NOT NULL "
                "AND source_table ~* '(whatsapp|wa_)' "
                "ORDER BY created_at, source_message_id"
            )
        ).mappings().all()

    results = []
    counters = {
        "examined": 0,
        "resolved_sender": 0,
        "resolved_row_ref": 0,
        "ambiguous": 0,
        "not_found": 0,
        "no_sender": 0,
        "would_update": 0,
        "updated": 0,
    }

    for source in sources:
        counters["examined"] += 1
        current_meta = _loads(source.get("source_metadata"), {})
        if not isinstance(current_meta, dict):
            current_meta = {}

        source_column = str(current_meta.get("source_column") or "").strip()
        if not source_column:
            source_column = "raw_message"

        lineage = _v19a_resolve_source_lineage(
            engine,
            str(source.get("source_table") or ""),
            source_column,
            str(source.get("raw_text") or ""),
        )

        status = str(lineage.get("sender_lineage_status") or "")
        if lineage.get("whatsapp_sender"):
            counters["resolved_sender"] += 1
        else:
            counters["no_sender"] += 1

        if lineage.get("source_row_ref"):
            counters["resolved_row_ref"] += 1

        if status == "AMBIGUOUS_SOURCE_ROWS":
            counters["ambiguous"] += 1
        if status == "SOURCE_ROW_NOT_FOUND":
            counters["not_found"] += 1

        merged = dict(current_meta)
        merged.update({
            "source_column": source_column,
            "source_table": source.get("source_table"),
            "sender_lineage_status": status,
            "source_row_ref": lineage.get("source_row_ref"),
            "source_row_ref_column": lineage.get("source_row_ref_column"),
            "source_match_count": lineage.get("match_count"),
        })

        if lineage.get("whatsapp_sender"):
            merged["whatsapp_sender"] = lineage["whatsapp_sender"]

        row_ref = lineage.get("source_row_ref") or source.get("source_row_ref")

        changed = (
            str(source.get("source_row_ref") or "") != str(row_ref or "")
            or _json_safe(current_meta) != _json_safe(merged)
        )

        if changed:
            counters["would_update"] += 1

        if changed and not dry_run:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE alliance_gold_source_messages "
                        "SET source_row_ref=:source_row_ref, "
                        "source_metadata=CAST(:source_metadata AS jsonb), "
                        "updated_at=now() "
                        "WHERE source_message_id=:source_message_id"
                    ),
                    {
                        "source_row_ref": row_ref,
                        "source_metadata": _json(merged),
                        "source_message_id": str(source["source_message_id"]),
                    },
                )
            counters["updated"] += 1

        results.append({
            "source_message_id": str(source["source_message_id"]),
            "source_table": source.get("source_table"),
            "status": status,
            "source_row_ref": lineage.get("source_row_ref"),
            "sender_phone": (lineage.get("whatsapp_sender") or {}).get("phone"),
            "changed": changed,
        })

    return {
        "status": "PASS",
        "version": VERSION,
        "dry_run": bool(dry_run),
        **counters,
        "items": results[:100],
        "academy_writes_only": True,
        "human_labels_modified": 0,
        "spans_resplit": 0,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }



# ---------------------------------------------------------------------------
# Foundation 1.9B: upstream WhatsApp sender lineage resolver
# ---------------------------------------------------------------------------

V19B_UPSTREAM_TABLE_RE = re.compile(
    r"(?:whatsapp|wa_|live_feed|message|event|inbox|chat)",
    re.I,
)

V19B_RAW_TEXT_COLUMNS = (
    "raw_message",
    "raw_text",
    "message",
    "message_text",
    "body",
    "text",
    "content",
)

def _v19b_candidate_tables(engine) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for table_name in _tables(engine):
        if table_name in ACADEMY_TABLES:
            continue
        if not V19B_UPSTREAM_TABLE_RE.search(table_name):
            continue
        try:
            cols = [c["column_name"] for c in _columns(engine, table_name)]
        except Exception:
            continue
        sender_cols = _v19_sender_phone_columns(cols)
        if not sender_cols:
            continue
        out.append({
            "table": table_name,
            "sender_phone_columns": sender_cols,
            "sender_name_columns": _v19_sender_name_columns(cols),
            "row_id_columns": _v19a_identifier_columns(cols),
            "raw_text_columns": [c for c in V19B_RAW_TEXT_COLUMNS if c in cols],
            "column_count": len(cols),
        })
    return out

def _v19b_fetch_unique_source_row(
    engine,
    source_table: str,
    source_column: str,
    raw_text: str,
) -> Dict[str, Any]:
    table_name = str(source_table or "").strip()
    column_name = str(source_column or "").strip()
    raw = str(raw_text or "")
    result = {
        "status": "UNRESOLVED",
        "source_table": table_name,
        "source_column": column_name,
        "row": None,
        "columns": [],
        "match_count": 0,
    }
    if not table_name or not column_name or not raw:
        result["status"] = "INSUFFICIENT_INPUT"
        return result

    columns = [c["column_name"] for c in _columns(engine, table_name)]
    result["columns"] = columns
    if column_name not in columns:
        result["status"] = "SOURCE_COLUMN_MISSING"
        return result

    qt = _safe_identifier(table_name)
    qc = _safe_identifier(column_name)
    select_sql = ", ".join(_safe_identifier(c) for c in columns)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT " + select_sql + " FROM " + qt + " "
                "WHERE " + qc + "::text = :raw LIMIT 3"
            ),
            {"raw": raw},
        ).mappings().all()

    result["match_count"] = len(rows)
    if len(rows) == 0:
        result["status"] = "SOURCE_ROW_NOT_FOUND"
    elif len(rows) > 1:
        result["status"] = "AMBIGUOUS_SOURCE_ROWS"
    else:
        result["status"] = "UNIQUE_SOURCE_ROW"
        result["row"] = dict(rows[0])
    return result

def _v19b_id_values(row: Dict[str, Any], columns: List[str]) -> Dict[str, str]:
    out = {}
    for col in _v19a_identifier_columns(columns):
        value = row.get(col)
        if value is None:
            continue
        sval = str(value).strip()
        if sval:
            out[col] = sval
    return out

def _v19b_extract_sender_from_rows(
    table_name: str,
    rows: List[Dict[str, Any]],
    phone_columns: List[str],
    name_columns: List[str],
    match_method: str,
    match_column: Optional[str] = None,
    match_value: Optional[str] = None,
) -> Dict[str, Any]:
    phones = set()
    names = set()
    used = set()

    for row in rows:
        for col in phone_columns:
            phone = _v19_phone_from_sender_value(row.get(col))
            if phone:
                phones.add(phone)
                used.add(col)
        for col in name_columns:
            value = str(row.get(col) or "").strip()
            if value:
                names.add(value)
                used.add(col)

    if len(phones) != 1:
        return {
            "status": "NO_UNIQUE_SENDER",
            "phones_found": sorted(phones),
            "row_count": len(rows),
        }

    return {
        "status": "FOUND_UNIQUE_SENDER",
        "phone": next(iter(phones)),
        "name": next(iter(names)) if len(names) == 1 else None,
        "source_table": table_name,
        "columns": sorted(used),
        "match_method": match_method,
        "match_column": match_column,
        "match_value": match_value,
        "row_count": len(rows),
    }

def _v19b_match_upstream_by_shared_ids(
    engine,
    upstream: Dict[str, Any],
    source_row: Dict[str, Any],
    source_columns: List[str],
) -> List[Dict[str, Any]]:
    table_name = upstream["table"]
    upstream_columns = [c["column_name"] for c in _columns(engine, table_name)]
    source_ids = _v19b_id_values(source_row, source_columns)
    candidates = []

    for source_col, source_value in source_ids.items():
        sl = source_col.lower()
        targets = []
        for target_col in upstream_columns:
            tl = target_col.lower()
            if tl == sl:
                targets.append(target_col)
            elif (
                ("message" in sl and "message" in tl and tl.endswith("_id"))
                or ("event" in sl and "event" in tl and tl.endswith("_id"))
                or ("source" in sl and "source" in tl and tl.endswith("_id"))
            ):
                targets.append(target_col)

        for target_col in targets:
            select_cols = []
            for c in (
                upstream["sender_phone_columns"]
                + upstream["sender_name_columns"]
                + upstream["row_id_columns"]
            ):
                if c not in select_cols:
                    select_cols.append(c)
            if not select_cols:
                continue

            qt = _safe_identifier(table_name)
            qc = _safe_identifier(target_col)
            select_sql = ", ".join(_safe_identifier(c) for c in select_cols)

            try:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT " + select_sql + " FROM " + qt + " "
                            "WHERE " + qc + "::text = :value LIMIT 3"
                        ),
                        {"value": source_value},
                    ).mappings().all()
            except Exception:
                continue

            if len(rows) != 1:
                continue

            sender = _v19b_extract_sender_from_rows(
                table_name,
                [dict(rows[0])],
                upstream["sender_phone_columns"],
                upstream["sender_name_columns"],
                "SHARED_ID",
                target_col,
                source_value,
            )
            if sender.get("status") == "FOUND_UNIQUE_SENDER":
                candidates.append(sender)

    return candidates

def _v19b_match_upstream_by_raw_text(
    engine,
    upstream: Dict[str, Any],
    raw_text: str,
) -> List[Dict[str, Any]]:
    table_name = upstream["table"]
    candidates = []

    for raw_col in upstream["raw_text_columns"]:
        select_cols = []
        for c in (
            upstream["sender_phone_columns"]
            + upstream["sender_name_columns"]
            + upstream["row_id_columns"]
        ):
            if c not in select_cols:
                select_cols.append(c)
        if not select_cols:
            continue

        qt = _safe_identifier(table_name)
        qc = _safe_identifier(raw_col)
        select_sql = ", ".join(_safe_identifier(c) for c in select_cols)

        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT " + select_sql + " FROM " + qt + " "
                        "WHERE " + qc + "::text = :raw LIMIT 3"
                    ),
                    {"raw": raw_text},
                ).mappings().all()
        except Exception:
            continue

        if len(rows) != 1:
            continue

        sender = _v19b_extract_sender_from_rows(
            table_name,
            [dict(rows[0])],
            upstream["sender_phone_columns"],
            upstream["sender_name_columns"],
            "EXACT_RAW_TEXT",
            raw_col,
            None,
        )
        if sender.get("status") == "FOUND_UNIQUE_SENDER":
            candidates.append(sender)

    return candidates

def _v19b_choose_unique_sender(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candidates:
        return {"status": "NO_UPSTREAM_SENDER_FOUND", "candidate_count": 0}

    phones = sorted({c.get("phone") for c in candidates if c.get("phone")})
    if len(phones) != 1:
        return {
            "status": "AMBIGUOUS_UPSTREAM_SENDERS",
            "candidate_count": len(candidates),
            "phones": phones,
            "candidates": candidates[:20],
        }

    phone = phones[0]
    preferred = sorted(
        [c for c in candidates if c.get("phone") == phone],
        key=lambda c: 0 if c.get("match_method") == "SHARED_ID" else 1,
    )[0]
    return {
        "status": "FOUND_UNIQUE_UPSTREAM_SENDER",
        "candidate_count": len(candidates),
        "sender": preferred,
        "all_supporting_paths": [
            {
                "source_table": c.get("source_table"),
                "match_method": c.get("match_method"),
                "match_column": c.get("match_column"),
            }
            for c in candidates if c.get("phone") == phone
        ][:20],
    }

def resolve_upstream_sender_for_gold_source(
    engine,
    source: Dict[str, Any],
    upstream_tables: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    source_table = str(source.get("source_table") or "")
    raw_text = str(source.get("raw_text") or "")
    metadata = _loads(source.get("source_metadata"), {})
    if not isinstance(metadata, dict):
        metadata = {}

    source_column = str(metadata.get("source_column") or "raw_message")
    result = {
        "source_message_id": str(source.get("source_message_id") or ""),
        "source_table": source_table,
        "source_column": source_column,
        "status": "UNRESOLVED",
        "sender": None,
        "source_row_status": None,
    }

    if not V19_WHATSAPP_SOURCE_RE.search(source_table):
        result["status"] = "NON_WHATSAPP_SOURCE"
        return result

    source_row = _v19b_fetch_unique_source_row(
        engine, source_table, source_column, raw_text
    )
    result["source_row_status"] = source_row.get("status")
    result["source_match_count"] = source_row.get("match_count")

    candidates = []
    upstream_tables = upstream_tables or _v19b_candidate_tables(engine)

    if source_row.get("status") == "UNIQUE_SOURCE_ROW":
        for upstream in upstream_tables:
            if upstream["table"] == source_table:
                continue
            candidates.extend(
                _v19b_match_upstream_by_shared_ids(
                    engine,
                    upstream,
                    source_row["row"],
                    source_row["columns"],
                )
            )

    if not candidates:
        for upstream in upstream_tables:
            if upstream["table"] == source_table:
                continue
            candidates.extend(
                _v19b_match_upstream_by_raw_text(
                    engine, upstream, raw_text
                )
            )

    chosen = _v19b_choose_unique_sender(candidates)
    result.update({
        "status": chosen.get("status"),
        "sender": chosen.get("sender"),
        "candidate_count": chosen.get("candidate_count", 0),
        "supporting_paths": chosen.get("all_supporting_paths", []),
        "ambiguous_phones": chosen.get("phones", []),
    })
    return result


# ---------------------------------------------------------------------------
# Foundation 1.9C: ambiguous normalized-row event lineage + name sanitizer
# ---------------------------------------------------------------------------

def _v19c_is_phone_like_name(value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False

    if _v19_phone_from_sender_value(raw):
        return True

    digits = re.sub(r"\D", "", raw)
    compact = re.sub(r"[\s+\-().]", "", raw)

    if len(digits) >= 10 and compact.isdigit():
        return True

    return False

def _v19c_clean_sender_name(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if _v19c_is_phone_like_name(raw):
        return None
    return raw

def _v19b_extract_sender_from_rows(
    table_name: str,
    rows: List[Dict[str, Any]],
    phone_columns: List[str],
    name_columns: List[str],
    match_method: str,
    match_column: Optional[str] = None,
    match_value: Optional[str] = None,
) -> Dict[str, Any]:
    phones = set()
    names = set()
    used_phone_columns = set()
    used_name_columns = set()
    rejected_phone_like_names = []

    for row in rows:
        for col in phone_columns:
            phone = _v19_phone_from_sender_value(row.get(col))
            if phone:
                phones.add(phone)
                used_phone_columns.add(col)

        for col in name_columns:
            raw_name = str(row.get(col) or "").strip()
            if not raw_name:
                continue
            clean_name = _v19c_clean_sender_name(raw_name)
            if clean_name:
                names.add(clean_name)
                used_name_columns.add(col)
            else:
                rejected_phone_like_names.append({
                    "column": col,
                    "value": raw_name,
                    "reason": "PHONE_LIKE_SENDER_NAME",
                })

    if len(phones) != 1:
        return {
            "status": "NO_UNIQUE_SENDER",
            "phones_found": sorted(phones),
            "row_count": len(rows),
            "rejected_sender_names": rejected_phone_like_names[:20],
        }

    evidence_columns = sorted(used_phone_columns | used_name_columns)

    return {
        "status": "FOUND_UNIQUE_SENDER",
        "phone": next(iter(phones)),
        "name": next(iter(names)) if len(names) == 1 else None,
        "source_table": table_name,
        "columns": evidence_columns,
        "phone_columns": sorted(used_phone_columns),
        "name_columns": sorted(used_name_columns),
        "rejected_sender_names": rejected_phone_like_names[:20],
        "match_method": match_method,
        "match_column": match_column,
        "match_value": match_value,
        "row_count": len(rows),
    }

def _v19c_fetch_source_rows(
    engine,
    source_table: str,
    source_column: str,
    raw_text: str,
    limit: int = 10,
) -> Dict[str, Any]:
    table_name = str(source_table or "").strip()
    column_name = str(source_column or "").strip()
    raw = str(raw_text or "")

    result: Dict[str, Any] = {
        "status": "UNRESOLVED",
        "source_table": table_name,
        "source_column": column_name,
        "rows": [],
        "columns": [],
        "match_count": 0,
    }

    if not table_name or not column_name or not raw:
        result["status"] = "INSUFFICIENT_INPUT"
        return result

    columns = [c["column_name"] for c in _columns(engine, table_name)]
    result["columns"] = columns

    if column_name not in columns:
        result["status"] = "SOURCE_COLUMN_MISSING"
        return result

    qt = _safe_identifier(table_name)
    qc = _safe_identifier(column_name)
    select_sql = ", ".join(_safe_identifier(c) for c in columns)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT " + select_sql + " FROM " + qt + " "
                "WHERE " + qc + "::text = :raw "
                "LIMIT :limit"
            ),
            {"raw": raw, "limit": max(1, min(int(limit), 50))},
        ).mappings().all()

    result["rows"] = [dict(r) for r in rows]
    result["match_count"] = len(rows)

    if len(rows) == 0:
        result["status"] = "SOURCE_ROW_NOT_FOUND"
    elif len(rows) == 1:
        result["status"] = "UNIQUE_SOURCE_ROW"
    else:
        result["status"] = "MULTIPLE_SOURCE_ROWS"

    return result

def _v19c_row_trace_summary(
    source_row: Dict[str, Any],
    source_columns: List[str],
) -> Dict[str, Any]:
    ids = _v19b_id_values(source_row, source_columns)
    return {
        "id_values": ids,
        "id_count": len(ids),
    }

def _v19c_match_all_source_rows_by_shared_ids(
    engine,
    upstream_tables: List[Dict[str, Any]],
    source_rows: List[Dict[str, Any]],
    source_columns: List[str],
    source_table: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    candidates: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []

    for index, row in enumerate(source_rows):
        row_candidates: List[Dict[str, Any]] = []

        for upstream in upstream_tables:
            if upstream["table"] == source_table:
                continue
            row_candidates.extend(
                _v19b_match_upstream_by_shared_ids(
                    engine,
                    upstream,
                    row,
                    source_columns,
                )
            )

        candidates.extend(row_candidates)
        traces.append({
            "source_row_index": index,
            **_v19c_row_trace_summary(row, source_columns),
            "resolved_candidate_count": len(row_candidates),
            "resolved_phones": sorted({
                c.get("phone")
                for c in row_candidates
                if c.get("phone")
            }),
            "paths": [
                {
                    "upstream_table": c.get("source_table"),
                    "match_method": c.get("match_method"),
                    "match_column": c.get("match_column"),
                    "phone": c.get("phone"),
                }
                for c in row_candidates
            ][:20],
        })

    return candidates, traces

def resolve_upstream_sender_for_gold_source(
    engine,
    source: Dict[str, Any],
    upstream_tables: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    source_table = str(source.get("source_table") or "")
    raw_text = str(source.get("raw_text") or "")
    metadata = _loads(source.get("source_metadata"), {})
    if not isinstance(metadata, dict):
        metadata = {}

    # Foundation 1.9H: source_column metadata is not guaranteed to exist.
    # ai_whatsapp_purity stores the evidence in raw_text, while older WhatsApp
    # sources may use raw_message/message_text/message/body/text. Resolve the
    # actual source evidence column from the live table schema before tracing
    # sender lineage. This is read-only.
    available_source_columns = [
        c["column_name"] for c in _columns(engine, source_table)
    ] if source_table else []
    requested_source_column = str(metadata.get("source_column") or "").strip()
    source_column_candidates = [
        requested_source_column,
        "raw_text",
        "raw_message",
        "message_text",
        "message",
        "body",
        "text",
    ]
    source_column = next(
        (
            c for c in source_column_candidates
            if c and c in available_source_columns
        ),
        requested_source_column or "raw_message",
    )
    result: Dict[str, Any] = {
        "source_message_id": str(source.get("source_message_id") or ""),
        "source_table": source_table,
        "source_column": source_column,
        "source_column_requested": requested_source_column or None,
        "source_column_available": available_source_columns,
        "status": "UNRESOLVED",
        "sender": None,
        "source_row_status": None,
        "source_rows_considered": 0,
        "id_candidate_count": 0,
        "raw_text_candidate_count": 0,
        "row_id_traces": [],
    }

    if not V19_WHATSAPP_SOURCE_RE.search(source_table):
        result["status"] = "NON_WHATSAPP_SOURCE"
        return result

    upstream_tables = upstream_tables or _v19b_candidate_tables(engine)

    source_rows = _v19c_fetch_source_rows(
        engine,
        source_table,
        source_column,
        raw_text,
        limit=10,
    )

    result["source_row_status"] = source_rows.get("status")
    result["source_match_count"] = source_rows.get("match_count")
    result["source_rows_considered"] = len(source_rows.get("rows") or [])

    id_candidates: List[Dict[str, Any]] = []
    row_traces: List[Dict[str, Any]] = []

    if source_rows.get("rows"):
        id_candidates, row_traces = _v19c_match_all_source_rows_by_shared_ids(
            engine,
            upstream_tables,
            source_rows["rows"],
            source_rows["columns"],
            source_table,
        )

    result["id_candidate_count"] = len(id_candidates)
    result["row_id_traces"] = row_traces

    if id_candidates:
        chosen = _v19b_choose_unique_sender(id_candidates)
        result.update({
            "status": chosen.get("status"),
            "sender": chosen.get("sender"),
            "candidate_count": chosen.get("candidate_count", 0),
            "supporting_paths": chosen.get("all_supporting_paths", []),
            "ambiguous_phones": chosen.get("phones", []),
            "resolution_stage": "EVENT_OR_MESSAGE_ID",
        })
        return result

    raw_candidates: List[Dict[str, Any]] = []
    for upstream in upstream_tables:
        if upstream["table"] == source_table:
            continue
        raw_candidates.extend(
            _v19b_match_upstream_by_raw_text(
                engine,
                upstream,
                raw_text,
            )
        )

    result["raw_text_candidate_count"] = len(raw_candidates)
    chosen = _v19b_choose_unique_sender(raw_candidates)

    result.update({
        "status": chosen.get("status"),
        "sender": chosen.get("sender"),
        "candidate_count": chosen.get("candidate_count", 0),
        "supporting_paths": chosen.get("all_supporting_paths", []),
        "ambiguous_phones": chosen.get("phones", []),
        "resolution_stage": (
            "UNIQUE_EXACT_RAW_TEXT"
            if raw_candidates
            else "UNRESOLVED"
        ),
    })
    return result


def upstream_sender_lineage_diagnostic(engine) -> Dict[str, Any]:
    candidates = _v19b_candidate_tables(engine)
    with engine.connect() as conn:
        sources = conn.execute(
            text(
                "SELECT source_message_id, source_table, source_row_ref, "
                "raw_text, source_metadata "
                "FROM alliance_gold_source_messages "
                "WHERE source_table IS NOT NULL "
                "AND source_table ~* '(whatsapp|wa_)' "
                "ORDER BY created_at, source_message_id"
            )
        ).mappings().all()

    items = []
    resolved = ambiguous = unresolved = 0
    for source in sources:
        item = resolve_upstream_sender_for_gold_source(
            engine, dict(source), candidates
        )
        if item["status"] == "FOUND_UNIQUE_UPSTREAM_SENDER":
            resolved += 1
        elif item["status"] == "AMBIGUOUS_UPSTREAM_SENDERS":
            ambiguous += 1
        else:
            unresolved += 1
        items.append(item)

    return {
        "status": "PASS",
        "version": VERSION,
        "mode": MODE,
        "upstream_candidate_tables": candidates,
        "gold_whatsapp_sources_examined": len(sources),
        "resolved_unique_sender": resolved,
        "ambiguous_sender": ambiguous,
        "unresolved_sender": unresolved,
        "items": items[:100],
        "read_only": True,
        "academy_writes": 0,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }

def backfill_upstream_sender_lineage(
    engine,
    dry_run: bool = True,
) -> Dict[str, Any]:
    candidates = _v19b_candidate_tables(engine)
    with engine.connect() as conn:
        sources = conn.execute(
            text(
                "SELECT source_message_id, source_table, source_row_ref, "
                "raw_text, source_metadata "
                "FROM alliance_gold_source_messages "
                "WHERE source_table IS NOT NULL "
                "AND source_table ~* '(whatsapp|wa_)' "
                "ORDER BY created_at, source_message_id"
            )
        ).mappings().all()

    items = []
    resolved = ambiguous = unresolved = would_update = updated = 0

    for source in sources:
        source_dict = dict(source)
        resolution = resolve_upstream_sender_for_gold_source(
            engine, source_dict, candidates
        )
        status = resolution.get("status")

        if status == "FOUND_UNIQUE_UPSTREAM_SENDER":
            resolved += 1
        elif status == "AMBIGUOUS_UPSTREAM_SENDERS":
            ambiguous += 1
        else:
            unresolved += 1

        changed = False

        if status == "FOUND_UNIQUE_UPSTREAM_SENDER":
            sender = resolution.get("sender") or {}
            metadata = _loads(source_dict.get("source_metadata"), {})
            if not isinstance(metadata, dict):
                metadata = {}

            new_sender = {
                "phone": sender.get("phone"),
                "name": sender.get("name"),
                "columns": sender.get("columns") or [],
                "role": "SOURCE_CONTACT",
                "provenance": "WHATSAPP_SENDER",
                "owner_status": "NOT_PROVEN",
                "broker_status": "NOT_PROVEN",
                "resolved_via": sender.get("match_method"),
                "upstream_table": sender.get("source_table"),
                "match_column": sender.get("match_column"),
            }

            merged = dict(metadata)
            merged["whatsapp_sender"] = new_sender
            merged["upstream_sender_lineage_status"] = status
            merged["upstream_sender_supporting_paths"] = resolution.get(
                "supporting_paths", []
            )

            changed = _json_safe(metadata) != _json_safe(merged)
            if changed:
                would_update += 1

            if changed and not dry_run:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "UPDATE alliance_gold_source_messages "
                            "SET source_metadata=CAST(:source_metadata AS jsonb), "
                            "updated_at=now() "
                            "WHERE source_message_id=:source_message_id"
                        ),
                        {
                            "source_metadata": _json(merged),
                            "source_message_id": str(
                                source_dict["source_message_id"]
                            ),
                        },
                    )
                updated += 1

        items.append({
            "source_message_id": str(source_dict["source_message_id"]),
            "source_table": source_dict.get("source_table"),
            "status": status,
            "sender_phone": (
                resolution.get("sender") or {}
            ).get("phone"),
            "upstream_table": (
                resolution.get("sender") or {}
            ).get("source_table"),
            "match_method": (
                resolution.get("sender") or {}
            ).get("match_method"),
            "changed": changed,
        })

    return {
        "status": "PASS",
        "version": VERSION,
        "dry_run": bool(dry_run),
        "examined": len(sources),
        "resolved_unique_sender": resolved,
        "ambiguous_sender": ambiguous,
        "unresolved_sender": unresolved,
        "would_update": would_update,
        "updated": updated,
        "items": items[:100],
        "academy_writes_only": True,
        "human_labels_modified": 0,
        "spans_resplit": 0,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }



# ---------------------------------------------------------------------------
# Foundation 1.9G: live sender-contact recovery + contact-lineage diagnostic
# ---------------------------------------------------------------------------

def _v19i_normalize_db_url(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _v19i_whatsapp_engine(primary_engine):
    whatsapp_url = str(os.getenv("WHATSAPP_DATABASE_URL") or "").strip()
    primary_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not whatsapp_url or whatsapp_url == primary_url:
        return primary_engine, False
    return create_engine(
        _v19i_normalize_db_url(whatsapp_url),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    ), True


def _v19i_exact_ai_whatsapp_purity_sender(engine, source: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "status": "UNRESOLVED",
        "resolution_stage": "AI_WHATSAPP_PURITY_EXACT_CROSS_DB",
        "candidate_count": 0,
        "sender": None,
        "read_only": True,
    }
    if str(source.get("source_table") or "") != "ai_whatsapp_purity":
        result["status"] = "NOT_AI_WHATSAPP_PURITY"
        return result

    row_ref = str(source.get("source_row_ref") or "").strip()
    raw_text = str(source.get("source_raw_text") or source.get("raw_text") or "")

    with engine.connect() as conn:
        purity_rows = []
        if row_ref:
            purity_rows = conn.execute(
                text("SELECT listing_id::text AS listing_id, raw_text FROM ai_whatsapp_purity WHERE listing_id::text=:row_ref LIMIT 2"),
                {"row_ref": row_ref},
            ).mappings().all()
        if not purity_rows and raw_text:
            purity_rows = conn.execute(
                text("SELECT listing_id::text AS listing_id, raw_text FROM ai_whatsapp_purity WHERE raw_text=:raw_text LIMIT 3"),
                {"raw_text": raw_text},
            ).mappings().all()

    result["candidate_count"] = len(purity_rows)
    if len(purity_rows) == 0:
        result["status"] = "PURITY_ROW_NOT_FOUND"
        return result
    if len(purity_rows) > 1:
        result["status"] = "AMBIGUOUS_PURITY_ROWS"
        return result

    listing_id = str(purity_rows[0].get("listing_id") or "").strip()
    result["source_listing_id"] = listing_id
    if not listing_id:
        result["status"] = "PURITY_LISTING_ID_MISSING"
        return result

    wa_engine = None
    dispose_wa = False
    try:
        wa_engine, dispose_wa = _v19i_whatsapp_engine(engine)
        with wa_engine.connect() as conn:
            has_listings = bool(conn.execute(text("SELECT to_regclass('wai_listings')")).scalar())
            has_raw = bool(conn.execute(text("SELECT to_regclass('wai_raw_messages')")).scalar())
            has_contacts = bool(conn.execute(text("SELECT to_regclass('wai_contacts')")).scalar())
            if not has_listings:
                result["status"] = "WAI_LISTINGS_NOT_FOUND"
                return result

            fields = ["l.id::text AS listing_id", "l.source_message_id::text AS source_message_id", "l.contact_id::text AS contact_id"]
            joins = []
            if has_raw:
                fields += ["rm.sender_phone", "rm.sender_display_name"]
                joins += ["LEFT JOIN wai_raw_messages rm ON rm.id=l.source_message_id"]
            else:
                fields += ["NULL::text AS sender_phone", "NULL::text AS sender_display_name"]
            if has_contacts:
                fields += ["ct.phone AS contact_phone", "ct.display_name AS contact_name", "ct.firm_name AS contact_firm"]
                joins += ["LEFT JOIN wai_contacts ct ON ct.id=l.contact_id"]
            else:
                fields += ["NULL::text AS contact_phone", "NULL::text AS contact_name", "NULL::text AS contact_firm"]

            sql = "SELECT " + ", ".join(fields) + " FROM wai_listings l " + " ".join(joins) + " WHERE l.id::text=:listing_id LIMIT 2"
            rows = conn.execute(text(sql), {"listing_id": listing_id}).mappings().all()

        result["candidate_count"] = len(rows)
        if len(rows) == 0:
            result["status"] = "WAI_LISTING_NOT_FOUND"
            return result
        if len(rows) > 1:
            result["status"] = "AMBIGUOUS_WAI_LISTING"
            return result

        row = dict(rows[0])
        sender_phone = _v19_phone_from_sender_value(row.get("sender_phone"))
        if sender_phone:
            result["status"] = "FOUND_UNIQUE_UPSTREAM_SENDER"
            result["resolution_stage"] = "EXACT_WAI_LISTING_TO_RAW_MESSAGE"
            result["sender"] = {
                "phone": sender_phone, "name": row.get("sender_display_name"), "company": None,
                "source_table": "wai_raw_messages", "match_method": "EXACT_SOURCE_MESSAGE_ID",
                "match_column": "sender_phone", "provenance": "WHATSAPP_SENDER",
            }
            return result

        contact_phone = _v19_phone_from_sender_value(row.get("contact_phone"))
        if contact_phone:
            result["status"] = "FOUND_UNIQUE_UPSTREAM_SENDER"
            result["resolution_stage"] = "EXACT_WAI_LISTING_TO_CONTACT"
            result["sender"] = {
                "phone": contact_phone, "name": row.get("contact_name"), "company": row.get("contact_firm"),
                "source_table": "wai_contacts", "match_method": "EXACT_CONTACT_ID",
                "match_column": "phone", "provenance": "SOURCE_CONTACT",
            }
            return result

        result["status"] = "EXACT_LINEAGE_HAS_NO_PHONE"
        return result
    except Exception as exc:
        result["status"] = "CROSS_DB_LOOKUP_ERROR"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
        return result
    finally:
        if dispose_wa and wa_engine is not None:
            wa_engine.dispose()



def _v19g_live_upstream_sender_contact(
    engine,
    proposal: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    p = dict(proposal or {})
    if p.get("contacts"):
        return p
    payload = {
        "source_message_id": source.get("source_message_id"),
        "source_table": source.get("source_table"),
        "source_row_ref": source.get("source_row_ref"),
        "source_raw_text": source.get("source_raw_text"),
        "raw_text": source.get("source_raw_text") or source.get("raw_text"),
        "source_metadata": source.get("source_metadata"),
    }
    resolution = _v19i_exact_ai_whatsapp_purity_sender(engine, payload)
    if resolution.get("status") != "FOUND_UNIQUE_UPSTREAM_SENDER":
        generic = resolve_upstream_sender_for_gold_source(engine, payload)
        if generic.get("status") == "FOUND_UNIQUE_UPSTREAM_SENDER" or resolution.get("status") == "NOT_AI_WHATSAPP_PURITY":
            resolution = generic

    p["sender_lineage_status"] = resolution.get("status")
    p["sender_lineage_resolution_stage"] = resolution.get("resolution_stage")
    p["sender_lineage_candidate_count"] = resolution.get("candidate_count", 0)
    if resolution.get("status") != "FOUND_UNIQUE_UPSTREAM_SENDER":
        return p

    sender = resolution.get("sender") or {}
    phone = _v19_phone_from_sender_value(sender.get("phone"))
    if not phone:
        return p
    provenance = sender.get("provenance") or "WHATSAPP_SENDER"
    p["contacts"] = [{
        "phone": phone,
        "name": _v19c_clean_sender_name(sender.get("name")),
        "company": sender.get("company"),
        "role": "SOURCE_CONTACT",
        "provenance": provenance,
        "scope": "SOURCE_MESSAGE_SENDER" if provenance == "WHATSAPP_SENDER" else "SOURCE_LISTING_CONTACT",
        "owner_status": "NOT_PROVEN",
        "broker_status": "NOT_PROVEN",
        "source_table": sender.get("source_table"),
        "resolved_via": sender.get("match_method"),
        "match_column": sender.get("match_column"),
        "lineage_resolution_stage": resolution.get("resolution_stage"),
    }]
    p["sender_contact_fallback_used"] = True
    p["sender_contact_is_owner"] = False
    p["sender_contact_is_broker"] = False
    p["sender_contact_live_recovery"] = True
    return p




def span_contact_lineage_diagnostic(engine, span_id: str) -> Dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT sp.span_id, sp.source_message_id, sp.proposed_text, "
                "s.raw_text AS source_raw_text, s.source_table, "
                "s.source_metadata, s.source_row_ref "
                "FROM alliance_gold_spans sp "
                "JOIN alliance_gold_source_messages s ON s.source_message_id=sp.source_message_id "
                "WHERE sp.span_id=:span_id"
            ),
            {"span_id": span_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "Span not found")

    source = dict(row)
    payload = {
        "source_message_id": source.get("source_message_id"),
        "source_table": source.get("source_table"),
        "source_row_ref": source.get("source_row_ref"),
        "source_raw_text": source.get("source_raw_text"),
        "raw_text": source.get("source_raw_text"),
        "source_metadata": source.get("source_metadata"),
    }
    resolution = _v19i_exact_ai_whatsapp_purity_sender(engine, payload)
    if resolution.get("status") != "FOUND_UNIQUE_UPSTREAM_SENDER":
        generic = resolve_upstream_sender_for_gold_source(engine, payload)
        if generic.get("status") == "FOUND_UNIQUE_UPSTREAM_SENDER" or resolution.get("status") == "NOT_AI_WHATSAPP_PURITY":
            resolution = generic

    return _json_safe({
        "status": "PASS", "version": VERSION, "span_id": str(source.get("span_id")),
        "source_message_id": str(source.get("source_message_id")), "source_table": source.get("source_table"),
        "source_row_ref": source.get("source_row_ref"), "resolution": resolution, "read_only": True,
        "academy_writes": 0, "human_labels_modified": 0, "canonical_writes": 0,
        "offer_writes": 0, "matcher_writes": 0, "whatsapp_live_writes": 0,
    })



# ---------------------------------------------------------------------------
# Foundation 1.6A: entity scope + inventory-group intelligence
# ---------------------------------------------------------------------------

V16_PLOT_RANGE_RE = re.compile(
    r"\bPLOT\s*NO\.?\s*(\d+)\s*(?:TO|-|–|—)\s*(\d+)\b",
    re.I,
)

V16_AREA_RANGE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*(?:-|–|—|TO)\s*(?P<max>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|SQYARD|SQYARDS|YARDS?)\b",
    re.I,
)

V16_AREA_OPTIONS_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*&\s*(?P<b>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|SQYARD|SQYARDS|YARDS?)\b",
    re.I,
)

def _v16_clean_group_text(text_value: str) -> str:
    return re.sub(r"[*_`#]+", " ", str(text_value or "")).strip()

def _v16_group_kind(text_value: str) -> Optional[str]:
    s = _v16_clean_group_text(text_value)

    if re.search(r"\bLUXURY\s+FLOORS?\b", s, re.I):
        return "LUXURY_FLOORS"
    if re.search(r"\bRETAIL\s+SHOPS?\b|\bSHOPS?\b", s, re.I):
        return "SHOPS"
    if re.search(r"\bOFFICE\s+SPACE\b|\bOFFICES?\b", s, re.I):
        return "OFFICE"
    if re.search(r"\bSCO\b", s, re.I):
        return "SCO"
    if re.search(r"\bAFFORDABLE\s+FLATS?\b|\bFLATS?\b", s, re.I):
        return "FLATS"
    if re.search(r"\bPLOTS?\b|\bPLOT\s*NO\b", s, re.I):
        return "PLOTS"

    # Plot-number ranges are inherently grouped plot inventory even when the
    # word "plots" is absent from the block.
    if V16_PLOT_RANGE_RE.search(s):
        return "PLOTS"

    return None

def _v16_is_inventory_group(text_value: str) -> bool:
    s = _v16_clean_group_text(text_value)

    if V16_PLOT_RANGE_RE.search(s):
        return True
    if V16_AREA_RANGE_RE.search(s):
        return True
    if V16_AREA_OPTIONS_RE.search(s) and re.search(r"\bPLOTS?\b", s, re.I):
        return True
    if re.search(r"\bMULTIPLE\s+PROJECTS?\b", s, re.I):
        return True
    if re.search(r"\bLUXURY\s+FLOORS?\b.*\bAVAILABLE\b.*(?:&|AND).*\bSECTOR\b", s, re.I | re.S):
        return True
    if re.search(
        r"^(?:PLOTS?|SHOPS?|SCO|RETAIL\s+SHOPS?|OFFICE\s+SPACE|AFFORDABLE\s+FLATS?)\b",
        s,
        re.I,
    ):
        return True
    return False

def _v16_inventory_group_fields(text_value: str) -> Dict[str, Any]:
    s = str(text_value or "")
    if not _v16_is_inventory_group(s):
        return {}

    group: Dict[str, Any] = {
        "entity_scope": "INVENTORY_GROUP",
        "do_not_expand_to_physical_properties": True,
    }

    kind = _v16_group_kind(s)
    if kind:
        group["inventory_kind"] = kind

    m = V16_PLOT_RANGE_RE.search(_v16_clean_group_text(s))
    if m:
        group["plot_number_range"] = {
            "from": int(m.group(1)),
            "to": int(m.group(2)),
            "evidence": m.group(0),
        }

    m = V16_AREA_RANGE_RE.search(_v16_clean_group_text(s))
    if m:
        lo = float(m.group("min"))
        hi = float(m.group("max"))
        group["area_range"] = {
            "min": int(lo) if lo.is_integer() else lo,
            "max": int(hi) if hi.is_integer() else hi,
            "unit": _normalized_area_unit(m.group("unit")),
            "evidence": m.group(0),
        }

    m = V16_AREA_OPTIONS_RE.search(_v16_clean_group_text(s))
    if m:
        a = float(m.group("a"))
        b = float(m.group("b"))
        group["area_options"] = [
            int(a) if a.is_integer() else a,
            int(b) if b.is_integer() else b,
        ]
        group["area_options_unit"] = _normalized_area_unit(m.group("unit"))
        group["area_options_evidence"] = m.group(0)

    configs = re.findall(r"\b(\d+(?:\.\d+)?)\s*BHK\b", _v16_clean_group_text(s), re.I)
    if configs:
        group["configuration_options"] = [
            (x[:-2] if x.endswith(".0") else x) + " BHK" for x in configs
        ]

    if re.search(r"\bMULTIPLE\s+PROJECTS?\b", _v16_clean_group_text(s), re.I):
        group["project_scope"] = "MULTIPLE_PROJECTS"

    return group

def _v16_enrich_proposal(text_value: str, proposal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = dict(proposal or propose_fields(text_value))
    group = _v16_inventory_group_fields(text_value)
    if not group:
        return p

    p["content_type_hint"] = "INVENTORY_GROUP"
    pf = dict(p.get("property_fields") or {})
    pf.update(group)
    p["property_fields"] = pf
    p["entity_scope"] = "INVENTORY_GROUP"
    p["human_review_required"] = True
    return p

def _v16_entity_group_split(raw: str) -> Optional[Dict[str, Any]]:
    lines = _line_ranges(raw)
    if not lines:
        return None

    trigger = (
        V16_PLOT_RANGE_RE.search(_v16_clean_group_text(raw))
        or re.search(r"\bMULTIPLE\s+PROJECTS?\b", _v16_clean_group_text(raw), re.I)
        or re.search(
            r"^\s*#\s*(?:\*|_)?(?:PLOTS?|SHOPS?|SCO|RETAIL|OFFICE|AFFORDABLE|LUXURY)",
            raw,
            re.I | re.M,
        )
    )
    if not trigger:
        return None

    starts: List[Tuple[int, int, str]] = []

    block_re = re.compile(
        r"^\s*[A-Z]\s*BLOCK\b.*\b\d+(?:\.\d+)?\s*"
        r"(?:SQYDS?|SQYD|SQ\.?\s*YDS?|YARDS?|SQFT|SFT)\b",
        re.I,
    )
    broad_re = re.compile(
        r"^\s*#\s*(?:\*|_)?(?:LUXURY\s+FLOORS?|PLOTS?|SHOPS?|SCO|"
        r"RETAIL\s+SHOPS?|OFFICE\s+SPACE|AFFORDABLE\s+FLATS?)\b",
        re.I,
    )

    for idx, (start, _end, line) in enumerate(lines):
        clean = re.sub(r"[*_`]+", "", str(line or "")).strip()
        if block_re.search(clean) or broad_re.search(clean):
            starts.append((start, idx, clean))

    if len(starts) < 2:
        return None

    children: List[Dict[str, Any]] = []
    ranges: List[Tuple[int, int]] = []

    for i, (start, line_idx, _clean) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(raw)

        for j in range(line_idx + 1, len(lines)):
            ls, _le, line = lines[j]
            if ls >= end:
                break
            clean = re.sub(r"[*_`]+", "", str(line or "")).strip()
            if re.fullmatch(r"(?:GURGAON|GURUGRAM)\s+PROPERTIES", clean, re.I):
                end = ls
                break
            if re.search(r"^FOR\s+ANY\s+QUERY\b|^FOR\s+MORE\s+DETAILS\b", clean, re.I):
                end = ls
                break

        block = raw[start:end]
        text_value = block.strip()
        if not text_value:
            continue

        left = len(block) - len(block.lstrip())
        exact_start = start + left
        exact_end = start + len(block.rstrip())
        proposal = _v16_enrich_proposal(text_value)

        children.append({
            "child_order": len(children) + 1,
            "start_offset": exact_start,
            "end_offset": exact_end,
            "text": text_value,
            "proposal": proposal,
            "context": {
                "boundary_strategy": "ENTITY_GROUP_BOUNDARY_V1_6A",
                "entity_scope": proposal.get("entity_scope") or "ATOMIC_OR_REVIEW",
            },
        })
        ranges.append((exact_start, exact_end))

    if len(children) < 2:
        return None

    return {
        "status": "PASS",
        "children": children,
        "shared_context": _context_from_ranges(raw, ranges),
        "boundary_strategy": "ENTITY_GROUP_BOUNDARY_V1_6A",
        "human_confirmation_required": True,
        "range_expansion_forbidden": True,
    }


# ---------------------------------------------------------------------------
# Foundation 1.9F: inline numbered atomic split support
# ---------------------------------------------------------------------------

V19F_INLINE_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?P<num>\d{1,2})(?:\)|\.(?=\s))\s*")
V19F_CONTACT_TAIL_RE = re.compile(
    r"\s+(?P<footer>[*_`]*(?:contact|call|for\s+details?|dm)\b.*)$",
    re.I | re.S,
)

def _v19f_inline_numbered_split(text_value: str) -> Optional[Dict[str, Any]]:
    raw = str(text_value or "")
    matches = list(V19F_INLINE_NUMBER_RE.finditer(raw))
    if len(matches) < 2:
        return None

    numbers = [int(m.group("num")) for m in matches]
    if numbers[0] != 1 or numbers != list(range(1, len(numbers) + 1)):
        return None

    # Numbered atomic inventory can be proven either by explicit property nouns
    # OR compact configurations such as 1BHK / 2BHK / 3BHK.
    has_property_signal = bool(
        re.search(r"\b(?:PLOTS?|FLATS?|SHOPS?|OFFICES?|VILLAS?|FLOORS?|APARTMENTS?|BUNGALOWS?|HOUSES?)\b", raw, re.I)
        or re.search(r"(?<!\d)\d+\s*BHK\b", raw, re.I)
        or PROPERTY_TYPE_RE.search(raw)
    )
    if not has_property_signal:
        return None

    prefix = raw[:matches[0].start()].strip()
    clean_prefix = re.sub(r"[*_`#]+", "", prefix)
    prefix_proposal = _v16_enrich_proposal(prefix) if prefix else {}
    transaction_hint = prefix_proposal.get("transaction_type_hint")
    project_hint = prefix_proposal.get("project_name_hint")

    m_project = re.search(
        r"\b(?:PLOTS?|FLATS?|SHOPS?|OFFICES?|VILLAS?|FLOORS?)\s+"
        r"(?:FOR\s+(?:SALE|RENT)\s+)?IN\s+(.+?)\s*$",
        clean_prefix,
        re.I,
    )
    if m_project:
        candidate = m_project.group(1).strip(" ,.-")
        if candidate and len(candidate) <= 100:
            project_hint = candidate

    if re.search(r"\bFOR\s+SALE\b", clean_prefix, re.I):
        transaction_hint = "SALE"
    elif re.search(r"\bFOR\s+RENT\b", clean_prefix, re.I):
        transaction_hint = "RENT"
    elif re.search(r"\b(?:RENTAL|RENT)\s+AVAILABLE\b", clean_prefix, re.I):
        transaction_hint = "RENT"
    elif re.search(r"\bSALE\s+AVAILABLE\b", clean_prefix, re.I):
        transaction_hint = "SALE"

    children = []
    shared_context = [prefix] if prefix else []

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        block = raw[start:end]

        if i == len(matches) - 1:
            footer_match = V19F_CONTACT_TAIL_RE.search(block)
            if footer_match:
                footer_abs_start = start + footer_match.start("footer")
                footer_text = raw[footer_abs_start:end].strip()
                if footer_text:
                    shared_context.append(footer_text)
                block = raw[start:footer_abs_start]

        child_text = block.strip()
        if not child_text:
            continue

        proposal = _v16_enrich_proposal(child_text)
        child_tx = str(proposal.get("transaction_type_hint") or "").strip().upper()
        if transaction_hint and child_tx in {"", "UNKNOWN", "AMBIGUOUS"}:
            proposal["transaction_type_hint"] = transaction_hint

        child_project = str(proposal.get("project_name_hint") or "").strip()
        if project_hint and not child_project:
            proposal["project_name_hint"] = project_hint

        proposal.setdefault("context_provenance", {})
        if isinstance(proposal.get("context_provenance"), dict):
            if transaction_hint and child_tx in {"", "UNKNOWN", "AMBIGUOUS"}:
                proposal["context_provenance"]["transaction_type_hint"] = "INHERITED_FROM_SOURCE_PREAMBLE"
            if project_hint and not child_project:
                proposal["context_provenance"]["project_name_hint"] = "INHERITED_FROM_SOURCE_PREAMBLE"

        children.append({
            "child_order": len(children) + 1,
            "text": child_text,
            "proposal": proposal,
            "context": {
                "boundary_strategy": "INLINE_NUMBERED_ATOMIC_V1_9J",
                "context_is_source_grounded": bool(prefix),
                "shared_preamble": prefix or None,
                "inherited_transaction": transaction_hint,
                "inherited_project": project_hint,
                "number_marker_style": "DOT_OR_PAREN",
            },
        })

    if len(children) < 2:
        return None

    return {
        "status": "PASS",
        "children": children,
        "shared_context": [x for x in shared_context if x],
        "boundary_strategy": "INLINE_NUMBERED_ATOMIC_V1_9J",
        "human_confirmation_required": True,
    }


# FOUNDATION_1_9M_PIN_HEADING_ATOMIC_SPLIT
def _v19m_pin_heading_split(text_value: str) -> Optional[Dict[str, Any]]:
    # Foundation 1.9P: every property map-pin heading owns following facts.
    raw = str(text_value or "")
    if not raw.strip():
        return None
    lines = _line_ranges(raw)

    def _pin_text(line: str) -> Optional[str]:
        original = str(line or "").strip()
        if not re.match(r"^\s*📍", original):
            return None
        cleaned = re.sub(r"^\s*📍\s*", "", original).strip()
        cleaned = re.sub(r"[*_`]+", "", cleaned).strip()
        return cleaned or None

    def _is_footer_pin(cleaned: str) -> bool:
        return bool(re.match(r"^(?:OFFICE|CONTACT|ADDRESS|BROKER\s+DETAILS?)\b", cleaned or "", re.I))

    def _is_true_footer(line: str) -> bool:
        original = str(line or "").strip()
        cleaned = _boundary_clean_line(original)
        pin = _pin_text(original)
        if pin and _is_footer_pin(pin):
            return True
        if ATOMIC_CONTEXT_START_RE.search(cleaned):
            return True
        return bool(re.search(
            r"^(?:CONTACT\b|CONTACT\s+US\b|OFFICE\b|BROKER\s+DETAILS?\b|"
            r"FOR\s+MORE\s+DETAILS\b|FOR\s+SITE\s+VISITS?\b|FOR\s+PROPERTY\s+VISITS?\b)",
            cleaned, re.I))

    property_pins = []
    footer_start = None
    for start_pos, end_pos, line in lines:
        pin = _pin_text(line)
        if not pin:
            continue
        if _is_footer_pin(pin):
            if footer_start is None:
                footer_start = start_pos
            continue
        property_pins.append((start_pos, end_pos, line))

    if len(property_pins) < 2:
        return None

    ranges = []
    children = []
    for idx, (start_pos, _heading_end, heading_line) in enumerate(property_pins):
        next_start = property_pins[idx + 1][0] if idx + 1 < len(property_pins) else (
            footer_start if footer_start is not None and footer_start > start_pos else len(raw))
        child_end = next_start

        for ls, _le, line in lines:
            if ls <= start_pos:
                continue
            if ls >= child_end:
                break
            if _is_true_footer(line):
                child_end = ls
                break

        child_start, child_end = _trim_atomic_block(raw, start_pos, child_end)
        if child_end <= child_start:
            continue
        block = raw[child_start:child_end]
        child_text = block.strip()
        if not child_text:
            continue
        exact_pos = raw.find(child_text, child_start, child_end + 1)
        if exact_pos < 0 or not re.match(r"^\s*📍", child_text):
            return {"status":"NO_AUTOMATIC_SPLIT","children":[],"shared_context":[],
                    "reason":"Unsafe pin-heading child boundary."}

        exact_start = exact_pos
        exact_end = exact_pos + len(child_text)
        proposal = _v16_enrich_proposal(child_text)
        ranges.append((exact_start, exact_end))
        children.append({
            "child_order": len(children) + 1,
            "start_offset": exact_start,
            "end_offset": exact_end,
            "text": child_text,
            "proposal": proposal,
            "context": {
                "boundary_strategy": "PIN_HEADING_OWNS_FOLLOWING_FACTS_1_9P",
                "source_heading": str(heading_line or "").strip(),
                "context_is_source_grounded": True,
            },
        })

    if len(children) < 2:
        return {"status":"NO_AUTOMATIC_SPLIT","children":[],"shared_context":[],
                "reason":"Fewer than two safe property pin children remained."}

    previous_end = -1
    for child in children:
        if int(child["start_offset"]) < previous_end:
            return {"status":"NO_AUTOMATIC_SPLIT","children":[],"shared_context":[],
                    "reason":"Pin-heading children overlapped."}
        previous_end = int(child["end_offset"])

    return {
        "status":"PASS",
        "children":children,
        "shared_context":_context_from_ranges(raw, ranges),
        "boundary_strategy":"PIN_HEADING_OWNS_FOLLOWING_FACTS_1_9P",
        "human_confirmation_required":True,
    }




# FOUNDATION_1_9R_SPARKLE_HEADING_SPLIT
def _v19r_sparkle_heading_split(text_value: str):
    """Split broker inventory where each atomic property begins with ✨.

    Safety:
    - activates only when at least two plausible ✨ property headings exist;
    - header/contact material before the first property remains shared context;
    - footer/contact material after the final property remains shared context;
    - every child is an exact, ordered, non-overlapping source substring;
    - no city/locality is inferred from broker service-area/header text.
    """
    raw = str(text_value or "")
    if not raw.strip() or "✨" not in raw:
        return None

    import re as _re

    # Match the full heading line, not facts beneath it.
    heading_matches = list(_re.finditer(r"(?m)^[ \t]*✨[ \t]*(?P<title>[^\r\n]+?)[ \t]*$", raw))
    if len(heading_matches) < 2:
        return None

    def _looks_like_property_title(title: str) -> bool:
        t = str(title or "").strip(" \t-–—:|")
        if not t:
            return False
        upper = t.upper()
        reject = (
            "CONTACT", "CALL ", "CALL:", "OFFICE", "ADDRESS", "FOR SITE",
            "FOR PROPERTY", "ENQUIR", "INVENTORY", "LISTING", "TEAM"
        )
        if any(x in upper for x in reject):
            return False
        # A sparkle heading should be a concise building/project name, optionally
        # followed by an explicit locality after a dash.
        return len(t) <= 120

    property_heads = [m for m in heading_matches if _looks_like_property_title(m.group("title"))]
    if len(property_heads) < 2:
        return None

    # Footer begins only on explicit contact/marketing footer lines after last property.
    footer_re = _re.compile(
        r"(?mi)^[ \t]*(?:━{3,}|[-_]{5,})?[ \t]*(?:\r?\n)?"
        r"[ \t]*(?:📞|📲|☎️?|CONTACT\b|FOR SITE VISITS?\b|FOR PROPERTY VISITS?\b|"
        r"FOR MORE (?:DETAILS|LISTINGS)\b|FOR .*ENQUIR(?:Y|IES)\b)"
    )

    first_start = property_heads[0].start()
    last_head = property_heads[-1]
    footer_match = footer_re.search(raw, last_head.end())
    footer_start = footer_match.start() if footer_match else len(raw)

    children = []
    for idx, head in enumerate(property_heads):
        start = head.start()
        end = property_heads[idx + 1].start() if idx + 1 < len(property_heads) else footer_start
        # Preserve exact source substring while trimming only inter-block whitespace.
        while end > start and raw[end - 1] in "\r\n \t":
            end -= 1
        if end <= start:
            return None
        child_text = raw[start:end]
        if raw[start:end] != child_text:
            return None

        title = str(head.group("title") or "").strip()
        project_name = title
        locality = None

        # Only explicit "PROJECT – LOCALITY" text is parsed. Generic broker header
        # locations such as "Juhu • Bandra • Khar & Nearby" are never inherited.
        parts = _re.split(r"\s+[–—-]\s+", title, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            project_name = parts[0].strip()
            locality = parts[1].strip()

        children.append({
            "text": child_text,
            "start_offset": start,
            "end_offset": end,
            "proposal": {
                "project_name_hint": project_name or None,
                "city_hint": None,
                "locality_hint": locality,
            },
        })

    # Strong source-grounding checks.
    previous_end = -1
    for child in children:
        s = int(child["start_offset"])
        e = int(child["end_offset"])
        if s < previous_end or raw[s:e] != child["text"]:
            return None
        previous_end = e

    shared_parts = []
    header = raw[:first_start].strip()
    footer = raw[footer_start:].strip() if footer_start < len(raw) else ""
    if header:
        shared_parts.append(header)
    if footer:
        shared_parts.append(footer)

    return {
        "status": "PASS",
        "reason": "Repeated sparkle project headings form atomic property blocks.",
        "boundary_strategy": "SPARKLE_HEADING_PROPERTY_BLOCK_1_9R",
        "children": children,
        "shared_context": "\n\n".join(shared_parts),
        "source_grounded": True,
    }



# FOUNDATION_1_9T_NATURAL_COMMERCIAL_ATOMIC_SPLIT
# FOUNDATION_1_9T2_PARTNERSHIP_TYPO_TOLERANCE
def _v19t_natural_commercial_heading_split(text_value: str):
    # Source-grounded splitter for long commercial/hospitality WhatsApp dumps.
    raw = str(text_value or "")
    if not raw.strip():
        return None

    line_ranges = _line_offsets(raw)
    if not line_ranges:
        return None

    system_event_re = re.compile(
        r"^(?:\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*-\s*)?.*"
        r"(?:\bwas added\b|\bwere added\b|\badded\b.*\+?91|\bleft\b|"
        r"\bchanged their phone number\b|\bmessage or add the new number\b)",
        re.I,
    )

    broker_footer_re = re.compile(
        r"^(?:DEALS\s+ONLY\s+IN\b|BROTAJIT\s+ASSOCIATES\b|"
        r"FOR\s+SITE\s+VISITS?\b|FOR\s+MORE\s+(?:DETAILS|LISTINGS)\b|"
        r"CONTACT\s+(?:US|BROKER|AGENT)\b)",
        re.I,
    )

    asset_re = re.compile(
        r"\b(?:RESTAURANT|RESTURANT|RESTRO(?:\s*[- ]?\s*BAR)?|RESTROBAR|"
        r"BAR|NIGHT\s*CLUB|CLUB|BANQUET(?:\s+HALL|\s+FARM)?|"
        r"HOTEL|MOTEL|RESORT|CAFE|CAFÉ|FARMHOUSE|GUEST\s+HOUSE)\b",
        re.I,
    )

    opportunity_re = re.compile(
        r"\b(?:ON\s+SET\s*UP\s+SALE|SET\s*UP\s+SALE|ON\s+SALE|FOR\s+SALE|"
        r"ON\s+LEASE|FOR\s+LEASE|AVAILABLE\s+ON\s+LEASE|"
        r"(?:PARTNERSHIP|PATNERSHIP)\s+(?:IS\s+)?(?:AVAILABLE|AVILABLE|AVAILBLE))\b",
        re.I,
    )

    def clean_heading(line: str) -> str:
        s = str(line or "").strip()
        s = re.sub(r"^[^A-Za-z0-9]+", "", s).strip()
        return s

    headings = []
    for start, end, line in line_ranges:
        s = clean_heading(line)
        if not s or len(s) > 220:
            continue
        if system_event_re.search(s) or broker_footer_re.search(s):
            continue
        if not asset_re.search(s):
            continue
        if not opportunity_re.search(s):
            continue
        if re.match(
            r"^(?:LOCATION|AREA|COVERED|OPEN|RENT|SECURITY|DEPOSIT|DEMAND|"
            r"COMMISSION|BROKAGE|FLOOR|FLOORS|ROOMS|PLOT\s+SIZE|BUILD\s*UP|"
            r"BUILD\s+UP\s+AREA|SEATING\s+CAPACITY|MONTHLY\s+SALE|YEARLY\s+TURNOVER)\s*[-:]",
            s,
            re.I,
        ):
            continue
        headings.append({"start": start, "end": end, "line": line})

    if len(headings) < 2:
        return None

    def first_context_start(block_start: int, block_end: int):
        for start, _end, line in line_ranges:
            if start <= block_start:
                continue
            if start >= block_end:
                break
            s = clean_heading(line)
            if not s:
                continue
            if system_event_re.search(s) or broker_footer_re.search(s):
                return start
        return None

    children = []
    ranges = []

    for idx, head in enumerate(headings):
        start = int(head["start"])
        next_start = int(headings[idx + 1]["start"]) if idx + 1 < len(headings) else len(raw)
        context_start = first_context_start(start, next_start)
        end = context_start if context_start is not None else next_start

        while end > start and raw[end - 1] in "\r\n \t":
            end -= 1

        if end <= start:
            return None

        child_text = raw[start:end]
        if not child_text.strip() or raw[start:end] != child_text:
            return None

        proposal = _v16_enrich_proposal(child_text)
        proposal.setdefault("entity_scope", "COMMERCIAL_HOSPITALITY_OPPORTUNITY")
        proposal.setdefault("context_provenance", {})
        proposal["context_provenance"]["atomic_boundary"] = (
            "NATURAL_COMMERCIAL_HEADING_SOURCE_TEXT"
        )

        children.append({
            "child_order": len(children) + 1,
            "start_offset": start,
            "end_offset": end,
            "text": child_text,
            "proposal": proposal,
            "context": {
                "boundary_strategy": "NATURAL_COMMERCIAL_HEADING_1_9T",
                "source_grounded": True,
            },
        })
        ranges.append((start, end))

    if len(children) < 2:
        return None

    previous_end = -1
    for child in children:
        start = int(child["start_offset"])
        end = int(child["end_offset"])
        if start < previous_end or raw[start:end] != child["text"]:
            return None
        previous_end = end

    return {
        "status": "PASS",
        "reason": (
            "Repeated natural-language restaurant/banquet/hotel opportunity "
            "headings form atomic commercial property blocks."
        ),
        "boundary_strategy": "NATURAL_COMMERCIAL_HEADING_1_9T",
        "children": children,
        "shared_context": _context_from_ranges(raw, ranges),
        "source_grounded": True,
        "human_confirmation_required": True,
    }


def automatic_atomic_split(text_value: str) -> Dict[str, Any]:
    raw = str(text_value or "")
    v19f = _v19f_inline_numbered_split(raw)
    if v19f is not None:
        return v19f
    v16 = _v16_entity_group_split(raw)
    if v16 is not None:
        return v16

    # Foundation 1.9M: repeated 📍 headings are real property boundaries.
    # Existing v19f/v16 precedence is preserved to avoid regressions.
    v19m = _v19m_pin_heading_split(raw)
    if v19m is not None:
        return v19m
    v19r = _v19r_sparkle_heading_split(raw)
    if v19r is not None:
        return v19r

    # Foundation 1.9T: natural-language commercial / hospitality inventory.
    v19t = _v19t_natural_commercial_heading_split(raw)
    if v19t is not None:
        return v19t

    # Foundation 1.5 deterministic atomic boundary engine.
    # Handles repeated explicit property headings and locality headers followed
    # by compact inventory bullets. Every child remains an exact ordered
    # substring of the parent evidence.
    lines = _line_ranges(raw)

    def clean_locality(line: str) -> Optional[str]:
        original = str(line or "").strip()
        if not original:
            return None
        if original.startswith("📍"):
            val = original.lstrip("📍").strip()
            val = re.sub(r"[*_`]+", "", val).strip()
            return val or None

        cleaned = re.sub(r"[*_`]+", "", original).strip()
        if (
            2 <= len(cleaned) <= 60
            and not PROPERTY_FACT_RE.search(cleaned)
            and not PHONE_RE.search(cleaned)
            and not MONEY_RE.search(cleaned)
            and not AREA_RE.search(cleaned)
            and re.search(r"[A-Za-z]", cleaned)
            and not re.search(
                r"\b(?:DIRECT CLIENT|INVENTORY|FOR MORE DETAILS|SITE VISITS?|"
                r"CONTACT|PICTURES?|AVAILABLE|OPTIONS?)\b",
                cleaned,
                re.I,
            )
            and re.fullmatch(r"[A-Za-z0-9 .&()/'-]{2,60}", cleaned)
        ):
            return cleaned
        return None

    def is_contact_or_footer(line: str) -> bool:
        c = _boundary_clean_line(line)
        return bool(
            ATOMIC_CONTEXT_START_RE.search(c)
            or PHONE_RE.search(line or "")
            or re.search(
                r"\b(?:FOR MORE DETAILS|SITE VISITS?|CONTACT|CALL|"
                r"GLOBAL HOMES|ASSOCIATES|REALTY|REALITY)\b",
                c,
                re.I,
            )
        )

    def is_compact_property_anchor(line: str) -> bool:
        s = str(line or "").strip()
        if not s:
            return False
        bullet = bool(re.match(r"^[•▪●◦·\-–—]\s*", s))
        cleaned = re.sub(r"^[•▪●◦·\-–—]\s*", "", s)
        area_signal = bool(
            AREA_RE.search(cleaned)
            or re.search(
                r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*"
                r"(?:SQ\.?\s*YARDS?|SQ\.?\s*YDS?|SQYDS?|SQYD|YARDS?|GAJ|"
                r"SQ\.?\s*FT|SQFT|SFT)\b",
                cleaned,
                re.I,
            )
        )
        if not area_signal:
            return False
        return bullet or cleaned.count("|") >= 1

    heading_starts: List[int] = []
    for start_pos, _end_pos, line in lines:
        clean = _boundary_clean_line(line)
        if ATOMIC_PROPERTY_HEADING_RE.search(clean):
            heading_starts.append(start_pos)

    if len(heading_starts) >= 2:
        ranges: List[Tuple[int, int]] = []
        children: List[Dict[str, Any]] = []
        for idx, start_pos in enumerate(heading_starts):
            raw_end = heading_starts[idx + 1] if idx + 1 < len(heading_starts) else len(raw)
            child_start, child_end = _trim_atomic_block(raw, start_pos, raw_end)
            if child_end <= child_start:
                continue
            child_text = raw[child_start:child_end].strip()
            if not child_text:
                continue
            ranges.append((child_start, child_end))
            children.append({
                "child_order": len(children) + 1,
                "start_offset": child_start,
                "end_offset": child_end,
                "text": child_text,
                "proposal": _v16_enrich_proposal(child_text),
                "context": {"boundary_strategy": "EXPLICIT_PROPERTY_HEADING"},
            })
        if len(children) >= 2:
            return {
                "status": "PASS",
                "children": children,
                "shared_context": _context_from_ranges(raw, ranges),
                "boundary_strategy": "EXPLICIT_PROPERTY_HEADING",
                "human_confirmation_required": True,
            }

    anchors: List[Dict[str, Any]] = []
    current_locality: Optional[str] = None

    for idx, (start_pos, end_pos, line) in enumerate(lines):
        loc = clean_locality(line)
        if loc:
            current_locality = loc
            continue

        if is_compact_property_anchor(line):
            anchors.append({
                "line_index": idx,
                "start_offset": start_pos,
                "line_end": end_pos,
                "locality": current_locality,
            })

    if len(anchors) < 2:
        return {
            "status": "NO_AUTOMATIC_SPLIT",
            "children": [],
            "shared_context": [],
            "reason": "Fewer than two safe property anchors were found.",
        }

    ranges: List[Tuple[int, int]] = []
    children: List[Dict[str, Any]] = []

    for i, anchor in enumerate(anchors):
        start_pos = int(anchor["start_offset"])
        next_anchor_start = int(anchors[i + 1]["start_offset"]) if i + 1 < len(anchors) else len(raw)

        child_end = next_anchor_start
        for j in range(int(anchor["line_index"]) + 1, len(lines)):
            ls, _le, line = lines[j]
            if ls >= next_anchor_start:
                break
            if clean_locality(line) or is_contact_or_footer(line):
                child_end = ls
                break

        block = raw[start_pos:child_end]
        child_text = block.strip()
        if not child_text:
            continue

        left_trim = len(block) - len(block.lstrip())
        right_trimmed = block.rstrip()
        exact_start = start_pos + left_trim
        exact_end = start_pos + len(right_trimmed)

        locality = anchor.get("locality")
        proposal = _v16_enrich_proposal(child_text)
        if locality and not proposal.get("locality_hint"):
            proposal["locality_hint"] = locality
            proposal.setdefault("context_provenance", {})
            proposal["context_provenance"]["locality_hint"] = "INHERITED_FROM_SOURCE_LOCALITY_HEADER"

        ranges.append((exact_start, exact_end))
        children.append({
            "child_order": len(children) + 1,
            "start_offset": exact_start,
            "end_offset": exact_end,
            "text": child_text,
            "proposal": proposal,
            "context": {
                "boundary_strategy": "LOCALITY_COMPACT_INVENTORY",
                "inherited_locality": locality,
                "context_is_source_grounded": bool(locality),
            },
        })

    if len(children) < 2:
        return {
            "status": "NO_AUTOMATIC_SPLIT",
            "children": [],
            "shared_context": [],
            "reason": "Compact inventory anchors were found but fewer than two safe children remained.",
        }

    return {
        "status": "PASS",
        "children": children,
        "shared_context": _context_from_ranges(raw, ranges),
        "boundary_strategy": "LOCALITY_COMPACT_INVENTORY",
        "human_confirmation_required": True,
    }


def split_preview(engine, span_id: str) -> Dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(text('''
            SELECT span_id, source_message_id, span_order,
                   proposed_start_offset, proposed_end_offset,
                   COALESCE(human_text, proposed_text) AS span_text,
                   COALESCE(span_status, 'ACTIVE') AS span_status,
                   boundary_status
            FROM alliance_gold_spans
            WHERE span_id=:span_id
        '''), {"span_id": span_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Span not found")
    if str(row["span_status"]).upper() != "ACTIVE":
        raise HTTPException(409, "Only ACTIVE spans can be split")
    preview = automatic_atomic_split(str(row["span_text"] or ""))
    return _json_safe({
        "status": preview.get("status"), "version": VERSION, "span_id": span_id,
        "children": preview.get("children") or [], "shared_context": preview.get("shared_context") or [],
        "reason": preview.get("reason"), "human_confirmation_required": True,
        "academy_write_only": True, "production_writes": False,
    })

def _locate_children(parent_text: str, children_payload: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not isinstance(children_payload, list) or len(children_payload) < 2:
        raise HTTPException(400, "At least two child spans are required")

    children: List[Dict[str, Any]] = []
    for item in children_payload:
        if isinstance(item, dict):
            value = str(item.get("text") or "").strip()
            context = item.get("context") if isinstance(item.get("context"), dict) else {}
            proposal = item.get("proposal") if isinstance(item.get("proposal"), dict) else {}
        else:
            value = str(item or "").strip()
            context = {}
            proposal = {}
        if value:
            children.append({"text": value, "context": context, "proposal": proposal})

    if len(children) < 2:
        raise HTTPException(400, "At least two non-empty child spans are required")

    located: List[Dict[str, Any]] = []
    cursor = 0
    ranges: List[Tuple[int, int]] = []
    for idx, child in enumerate(children, start=1):
        child_text = child["text"]
        start = parent_text.find(child_text, cursor)
        if start < 0:
            raise HTTPException(400, f"Child {idx} is not an exact ordered substring of the parent span. Do not rewrite evidence while splitting; only cut the original text.")
        end = start + len(child_text)
        located.append({
            "child_order": idx,
            "start_offset": start,
            "end_offset": end,
            "text": child_text,
            "context": child.get("context") or {},
            "proposal": child.get("proposal") or {},
        })
        ranges.append((start, end))
        cursor = end
    return located, _context_from_ranges(parent_text, ranges)

def _active_label_count(conn, span_ids: List[str]) -> int:
    if not span_ids:
        return 0
    return int(conn.execute(text('''
        SELECT count(*) FROM alliance_gold_span_labels
        WHERE active=TRUE AND span_id = ANY(CAST(:ids AS uuid[]))
    '''), {"ids": span_ids}).scalar() or 0)

def _deactivate_lineage_dependencies(conn, span_ids: List[str]) -> None:
    if not span_ids:
        return
    conn.execute(text('''
        UPDATE alliance_gold_relationship_labels
        SET active=FALSE
        WHERE active=TRUE AND (
            left_span_id = ANY(CAST(:ids AS uuid[]))
            OR right_span_id = ANY(CAST(:ids AS uuid[]))
        )
    '''), {"ids": span_ids})

def _renumber_active_replacement(conn, source_message_id: str, parent_order: int, replacement_ids: List[str], removed_ids: List[str]) -> None:
    rows = conn.execute(text('''
        SELECT span_id, span_order FROM alliance_gold_spans
        WHERE source_message_id=:sid AND COALESCE(span_status, 'ACTIVE')='ACTIVE'
        ORDER BY span_order, created_at, span_id
    '''), {"sid": source_message_id}).mappings().all()
    removed = set(removed_ids)
    replacement = set(replacement_ids)
    before = [str(r["span_id"]) for r in rows if str(r["span_id"]) not in removed and str(r["span_id"]) not in replacement and int(r["span_order"]) < int(parent_order)]
    after = [str(r["span_id"]) for r in rows if str(r["span_id"]) not in removed and str(r["span_id"]) not in replacement and int(r["span_order"]) > int(parent_order)]
    sequence = before + replacement_ids + after
    conn.execute(text('''UPDATE alliance_gold_spans SET span_order = span_order + 1000000 WHERE source_message_id=:sid'''), {"sid": source_message_id})
    for new_order, sid in enumerate(sequence, start=1):
        conn.execute(text("UPDATE alliance_gold_spans SET span_order=:o WHERE span_id=:sid"), {"o": new_order, "sid": sid})

def _refresh_source_after_boundary_edit(conn, source_message_id: str) -> None:
    active_count = int(conn.execute(text('''
        SELECT count(*) FROM alliance_gold_spans
        WHERE source_message_id=:sid AND COALESCE(span_status, 'ACTIVE')='ACTIVE'
    '''), {"sid": source_message_id}).scalar() or 0)
    conn.execute(text('''
        UPDATE alliance_gold_source_messages
        SET proposed_span_count=:n,
            labeling_status=CASE
                WHEN NOT EXISTS (
                    SELECT 1 FROM alliance_gold_spans sp
                    WHERE sp.source_message_id=:sid
                      AND COALESCE(sp.span_status, 'ACTIVE')='ACTIVE'
                      AND sp.boundary_status <> 'LABELED'
                ) THEN 'LABELED' ELSE 'IN_PROGRESS' END,
            updated_at=now()
        WHERE source_message_id=:sid
    '''), {"n": active_count, "sid": source_message_id})

def split_span(engine, span_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    labeler_id = str(payload.get("labeler_id") or "").strip()
    if not labeler_id:
        raise HTTPException(400, "labeler_id is required")
    invalidate_existing = bool(payload.get("invalidate_existing_labels", False))
    reason = str(payload.get("reason") or "").strip() or "Human atomic boundary split"
    with engine.begin() as conn:
        parent = conn.execute(text('''
            SELECT span_id, source_message_id, span_order,
                   proposed_start_offset, proposed_end_offset,
                   COALESCE(human_text, proposed_text) AS span_text,
                   COALESCE(span_status, 'ACTIVE') AS span_status,
                   boundary_status
            FROM alliance_gold_spans WHERE span_id=:span_id FOR UPDATE
        '''), {"span_id": span_id}).mappings().first()
        if not parent:
            raise HTTPException(404, "Span not found")
        if str(parent["span_status"]).upper() != "ACTIVE":
            raise HTTPException(409, "Only ACTIVE spans can be split")
        active_labels = _active_label_count(conn, [span_id])
        if active_labels and not invalidate_existing:
            raise HTTPException(409, "This span already has an active Gold label. Set invalidate_existing_labels=true only after explicit human correction.")
        parent_text = str(parent["span_text"] or "")
        located, shared_context = _locate_children(parent_text, payload.get("children"))
        if active_labels:
            conn.execute(text('''
                UPDATE alliance_gold_span_labels
                SET active=FALSE,
                    notes=concat_ws(E'\\n', NULLIF(notes, ''), :audit_note),
                    updated_at=now()
                WHERE span_id=:span_id AND active=TRUE
            '''), {"span_id": span_id, "audit_note": f"[Foundation 1.4 boundary invalidation by {labeler_id}] {reason}"})
        _deactivate_lineage_dependencies(conn, [span_id])
        max_order = int(conn.execute(text("SELECT COALESCE(max(span_order),0) FROM alliance_gold_spans WHERE source_message_id=:sid"), {"sid": str(parent["source_message_id"])}).scalar() or 0)
        child_ids: List[str] = []
        base_start = int(parent["proposed_start_offset"] or 0)
        for idx, child in enumerate(located, start=1):
            child_id = str(uuid.uuid4())
            child_ids.append(child_id)
            conn.execute(text('''
                INSERT INTO alliance_gold_spans (
                    span_id, source_message_id, span_order,
                    proposed_start_offset, proposed_end_offset,
                    proposed_text, proposal_method, proposal_confidence,
                    parent_span_id, span_status, boundary_status, lineage_metadata
                ) VALUES (
                    :span_id, :source_message_id, :span_order,
                    :start_offset, :end_offset,
                    :proposed_text, 'HUMAN_ATOMIC_SPLIT_V1_4', 1.0,
                    :parent_span_id, 'ACTIVE', 'PENDING', CAST(:lineage_metadata AS jsonb)
                )
            '''), {
                "span_id": child_id, "source_message_id": str(parent["source_message_id"]),
                "span_order": max_order + idx,
                "start_offset": base_start + int(child["start_offset"]),
                "end_offset": base_start + int(child["end_offset"]),
                "proposed_text": child["text"], "parent_span_id": span_id,
                "lineage_metadata": _json({
                    "created_by": labeler_id,
                    "operation": "SPLIT",
                    "reason": reason,
                    "parent_span_id": span_id,
                    "source_grounded_context": child.get("context") or {},
                    "preview_proposal_context": {
                        k: v for k, v in (child.get("proposal") or {}).items()
                        if k in {"locality_hint", "city_hint", "project_name_hint", "context_provenance", "entity_scope"}
                    },
                }),
            })
        conn.execute(text('''
            UPDATE alliance_gold_spans
            SET span_status='SUPERSEDED', boundary_status='SUPERSEDED', boundary_action='SPLIT',
                superseded_at=now(), superseded_by=CAST(:children AS jsonb),
                lineage_metadata=COALESCE(lineage_metadata, '{}'::jsonb) || CAST(:meta AS jsonb),
                updated_at=now()
            WHERE span_id=:span_id
        '''), {
            "span_id": span_id, "children": _json(child_ids),
            "meta": _json({"operation": "SPLIT", "labeler_id": labeler_id, "reason": reason, "shared_context": shared_context, "invalidated_active_labels": active_labels}),
        })
        _renumber_active_replacement(conn, str(parent["source_message_id"]), int(parent["span_order"]), child_ids, [span_id])
        _refresh_source_after_boundary_edit(conn, str(parent["source_message_id"]))
    return _json_safe({
        "status": "SPLIT", "version": VERSION, "parent_span_id": span_id,
        "child_span_ids": child_ids, "child_count": len(child_ids), "shared_context": shared_context,
        "invalidated_active_labels": active_labels, "academy_write_only": True,
        "canonical_writes": 0, "offer_writes": 0, "matcher_writes": 0, "whatsapp_live_writes": 0,
    })

def merge_with_next(engine, span_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    labeler_id = str(payload.get("labeler_id") or "").strip()
    if not labeler_id:
        raise HTTPException(400, "labeler_id is required")
    invalidate_existing = bool(payload.get("invalidate_existing_labels", False))
    reason = str(payload.get("reason") or "").strip() or "Human atomic boundary merge"
    with engine.begin() as conn:
        current = conn.execute(text('''
            SELECT span_id, source_message_id, span_order, proposed_start_offset, proposed_end_offset,
                   COALESCE(span_status, 'ACTIVE') AS span_status
            FROM alliance_gold_spans WHERE span_id=:span_id FOR UPDATE
        '''), {"span_id": span_id}).mappings().first()
        if not current:
            raise HTTPException(404, "Span not found")
        if str(current["span_status"]).upper() != "ACTIVE":
            raise HTTPException(409, "Only ACTIVE spans can be merged")
        nxt = conn.execute(text('''
            SELECT span_id, source_message_id, span_order, proposed_start_offset, proposed_end_offset
            FROM alliance_gold_spans
            WHERE source_message_id=:sid AND COALESCE(span_status, 'ACTIVE')='ACTIVE' AND span_order > :o
            ORDER BY span_order LIMIT 1 FOR UPDATE
        '''), {"sid": str(current["source_message_id"]), "o": int(current["span_order"])}).mappings().first()
        if not nxt:
            raise HTTPException(409, "There is no next ACTIVE span in this source message")
        merge_ids = [span_id, str(nxt["span_id"])]
        active_labels = _active_label_count(conn, merge_ids)
        if active_labels and not invalidate_existing:
            raise HTTPException(409, "One or both spans already have active Gold labels. Set invalidate_existing_labels=true only after explicit human correction.")
        if active_labels:
            conn.execute(text('''
                UPDATE alliance_gold_span_labels
                SET active=FALSE, notes=concat_ws(E'\\n', NULLIF(notes,''), CAST(:audit_note AS text)), updated_at=now()
                WHERE span_id = ANY(CAST(:ids AS uuid[])) AND active=TRUE
            '''), {"ids": merge_ids, "audit_note": f"[Foundation 1.4 merge invalidation by {labeler_id}] {reason}"})
        source_raw = str(conn.execute(text("SELECT raw_text FROM alliance_gold_source_messages WHERE source_message_id=:sid"), {"sid": str(current["source_message_id"])}).scalar() or "")
        start_offset = min(int(current["proposed_start_offset"]), int(nxt["proposed_start_offset"]))
        end_offset = max(int(current["proposed_end_offset"]), int(nxt["proposed_end_offset"]))
        merged_text = source_raw[start_offset:end_offset].strip()
        if not merged_text:
            raise HTTPException(409, "Could not reconstruct merged evidence from source message")
        _deactivate_lineage_dependencies(conn, merge_ids)
        max_order = int(conn.execute(text("SELECT COALESCE(max(span_order),0) FROM alliance_gold_spans WHERE source_message_id=:sid"), {"sid": str(current["source_message_id"])}).scalar() or 0)
        merged_id = str(uuid.uuid4())
        conn.execute(text('''
            INSERT INTO alliance_gold_spans (
                span_id, source_message_id, span_order, proposed_start_offset, proposed_end_offset,
                proposed_text, proposal_method, proposal_confidence, parent_span_id,
                span_status, boundary_status, lineage_metadata
            ) VALUES (
                :span_id, :sid, :span_order, :start_offset, :end_offset,
                :proposed_text, 'HUMAN_ATOMIC_MERGE_V1_4', 1.0, NULL,
                'ACTIVE', 'PENDING', CAST(:meta AS jsonb)
            )
        '''), {
            "span_id": merged_id, "sid": str(current["source_message_id"]), "span_order": max_order + 1,
            "start_offset": start_offset, "end_offset": end_offset, "proposed_text": merged_text,
            "meta": _json({"created_by": labeler_id, "operation": "MERGE", "reason": reason, "merged_from": merge_ids}),
        })
        for old_id in merge_ids:
            conn.execute(text('''
                UPDATE alliance_gold_spans
                SET span_status='SUPERSEDED', boundary_status='SUPERSEDED', boundary_action='MERGE',
                    superseded_at=now(), superseded_by=CAST(:new_id AS jsonb),
                    lineage_metadata=COALESCE(lineage_metadata, '{}'::jsonb) || CAST(:meta AS jsonb), updated_at=now()
                WHERE span_id=:old_id
            '''), {
                "old_id": old_id, "new_id": _json([merged_id]),
                "meta": _json({"operation": "MERGE", "labeler_id": labeler_id, "reason": reason, "merged_into": merged_id, "invalidated_active_labels": active_labels}),
            })
        _renumber_active_replacement(conn, str(current["source_message_id"]), int(current["span_order"]), [merged_id], merge_ids)
        _refresh_source_after_boundary_edit(conn, str(current["source_message_id"]))
    return _json_safe({
        "status": "MERGED", "version": VERSION, "merged_span_id": merged_id,
        "superseded_span_ids": merge_ids, "invalidated_active_labels": active_labels,
        "academy_write_only": True, "canonical_writes": 0, "offer_writes": 0, "matcher_writes": 0, "whatsapp_live_writes": 0,
    })


# ---------------------------------------------------------------------------
# Foundation 1.9D: retroactive governing locality recovery for legacy spans
# ---------------------------------------------------------------------------

V19D_NON_LOCALITY_HEADER_RE = re.compile(
    r"\b(?:INVENTORY|OPTIONS?|DIRECT\s+CLIENT|AVAILABLE|PROPERTY|PROPERTIES|"
    r"FOR\s+SALE|FOR\s+RENT|DETAILS?|SITE\s+VISITS?|CONTACT|CALL|DM|QUERY)\b",
    re.I,
)
V19D_PORTFOLIO_COVERAGE_RE = re.compile(
    r"(?:\s[•|]\s|\s+&\s+|\bAND\s+NEARBY\b|\bNEARBY\b)",
    re.I,
)
V19D_BULLET_RE = re.compile(r"^\s*[•●▪◦*\-–—]\s*")

def _v19d_clean_locality_header(line: str) -> Optional[str]:
    raw = str(line or "").strip()
    if not raw or V19D_BULLET_RE.match(raw):
        return None
    had_pin = raw.startswith("📍")

    # Foundation 1.9E safety rule: retroactive locality recovery is only
    # allowed from an explicit pinned locality header. Legacy unpinned lines
    # can be plot identifiers, projects, sectors, inventory headings, or
    # residue from a previous group and must never leak into later spans.
    if not had_pin:
        return None

    clean = re.sub(r"^\s*📍\s*", "", raw).strip()
    clean = re.sub(r"[*_`#]+", "", clean).strip()
    if not clean:
        return None
    if V19D_PORTFOLIO_COVERAGE_RE.search(clean):
        return None
    if PROPERTY_FACT_RE.search(clean) or PHONE_RE.search(clean) or MONEY_RE.search(clean) or AREA_RE.search(clean):
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&()/'\-]{1,70}", clean):
        return None
    return re.sub(r"\s+", " ", clean).strip(" .:-") or None

def _v19d_find_span_occurrence(source_raw: str, span_text: str, proposed_start_offset: Any = None) -> Optional[Tuple[int, int]]:
    raw = str(source_raw or "")
    needle = str(span_text or "")
    if not raw or not needle:
        return None
    try:
        hint = int(proposed_start_offset)
    except Exception:
        hint = None
    if hint is not None and 0 <= hint < len(raw) and raw[hint:hint + len(needle)] == needle:
        return hint, hint + len(needle)
    starts: List[int] = []
    cursor = 0
    while True:
        pos = raw.find(needle, cursor)
        if pos < 0:
            break
        starts.append(pos)
        cursor = pos + max(1, len(needle))
    if len(starts) == 1:
        pos = starts[0]
        return pos, pos + len(needle)
    if starts and hint is not None:
        pos = min(starts, key=lambda x: abs(x - hint))
        return pos, pos + len(needle)
    return None

def _v19d_governing_locality(source_raw: str, span_text: str, proposed_start_offset: Any = None) -> Dict[str, Any]:
    raw = str(source_raw or "")
    occurrence = _v19d_find_span_occurrence(raw, span_text, proposed_start_offset)
    if not occurrence:
        return {"status": "SPAN_NOT_UNIQUELY_LOCATED", "locality": None, "provenance": None}
    span_start, span_end = occurrence
    active_locality = None
    active_header_text = None
    active_header_start = None
    for line_start, _line_end, line in _line_ranges(raw):
        if line_start >= span_start:
            break
        locality = _v19d_clean_locality_header(line)
        if locality:
            active_locality = locality
            active_header_text = str(line or "").strip()
            active_header_start = line_start
    if not active_locality:
        return {"status": "NO_GOVERNING_LOCALITY_HEADER", "locality": None, "provenance": None, "span_start": span_start, "span_end": span_end}
    return {
        "status": "FOUND_GOVERNING_LOCALITY",
        "locality": active_locality,
        "provenance": "RETROACTIVE_SOURCE_LOCALITY_HEADER",
        "header_text": active_header_text,
        "header_start": active_header_start,
        "span_start": span_start,
        "span_end": span_end,
    }

def _v19d_merge_retroactive_locality(proposal: Dict[str, Any], source_raw: str, span_text: str, proposed_start_offset: Any = None) -> Dict[str, Any]:
    proposal = dict(proposal or {})
    if proposal.get("locality_hint"):
        return proposal
    recovered = _v19d_governing_locality(source_raw, span_text, proposed_start_offset)
    if recovered.get("status") != "FOUND_GOVERNING_LOCALITY":
        return proposal
    proposal["locality_hint"] = recovered["locality"]
    proposal["source_grounded_inherited_locality"] = recovered["locality"]
    proposal["retroactive_locality_recovery"] = True
    proposal.setdefault("context_provenance", {})
    if isinstance(proposal["context_provenance"], dict):
        proposal["context_provenance"]["locality_hint"] = recovered["provenance"]
        proposal["context_provenance"]["locality_header_evidence"] = recovered.get("header_text")
    return proposal

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
                    (SELECT count(*) FROM alliance_gold_spans WHERE COALESCE(span_status, 'ACTIVE')='ACTIVE') AS proposed_spans,
                    (SELECT count(*) FROM alliance_gold_spans WHERE COALESCE(span_status, 'ACTIVE')='ACTIVE' AND boundary_status='LABELED') AS labeled_spans,
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

def next_span(
    engine,
    labeler_id: Optional[str] = None,
    skip_span_ids: Optional[str] = None,
) -> Dict[str, Any]:
    raw_skip_ids = [
        x.strip()
        for x in str(skip_span_ids or "").split(",")
        if x.strip()
    ]
    valid_skip_ids = [
        x for x in raw_skip_ids
        if re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
            x,
        )
    ][:500]
    skip_csv = ",".join(valid_skip_ids)

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
                    sp.lineage_metadata,
                    s.raw_text AS source_raw_text,
                    s.source_table,
                    s.source_row_ref,
                    s.source_metadata,
                    s.sampling_bucket,
                    s.message_length
                FROM alliance_gold_spans sp
                JOIN alliance_gold_source_messages s
                  ON s.source_message_id=sp.source_message_id
                WHERE COALESCE(sp.span_status, 'ACTIVE')='ACTIVE'
                  AND sp.boundary_status <> 'LABELED'
                  AND (
                      :skip_csv = ''
                      OR NOT (
                          sp.span_id::text = ANY(string_to_array(:skip_csv, ','))
                      )
                  )
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
            ),
            {"skip_csv": skip_csv},
        ).mappings().first()

    if not row:
        return {"status": "EMPTY", "message": "No unlabeled spans available."}

    out = dict(row)
    out["span_id"] = str(out["span_id"])
    out["source_message_id"] = str(out["source_message_id"])
    proposal = _v16_enrich_proposal(out["proposed_text"])
    lineage = _loads(out.get("lineage_metadata"), {})
    if not isinstance(lineage, dict):
        lineage = {}

    source_context = lineage.get("source_grounded_context") or {}
    preview_context = lineage.get("preview_proposal_context") or {}

    if isinstance(preview_context, dict):
        for key in ("locality_hint", "city_hint", "project_name_hint", "entity_scope"):
            if not proposal.get(key) and preview_context.get(key):
                proposal[key] = preview_context.get(key)
        if preview_context.get("context_provenance"):
            proposal.setdefault("context_provenance", {})
            if isinstance(preview_context.get("context_provenance"), dict):
                proposal["context_provenance"].update(preview_context["context_provenance"])

    if isinstance(source_context, dict):
        inherited_locality = source_context.get("inherited_locality")
        if inherited_locality and not proposal.get("locality_hint"):
            proposal["locality_hint"] = inherited_locality
            proposal.setdefault("context_provenance", {})
            proposal["context_provenance"]["locality_hint"] = "INHERITED_FROM_SOURCE_LOCALITY_HEADER"
        if inherited_locality:
            proposal["source_grounded_inherited_locality"] = inherited_locality

    # Foundation 1.9D: recover governing locality for pre-1.7 split children
    # from the original source message. Proposal-time only: no DB write.
    proposal = _v19d_merge_retroactive_locality(
        proposal,
        str(out.get("source_raw_text") or ""),
        str(out.get("proposed_text") or ""),
        out.get("proposed_start_offset"),
    )

    # Foundation 1.8D recovers shared footer contacts from the ORIGINAL
    # source message, including child spans created before Foundation 1.8.
    proposal = _v18_merge_source_contacts(
        proposal,
        str(out.get("source_raw_text") or ""),
    )

    # Foundation 1.9A: persisted sender lineage has priority over
    # rediscovery by raw message text.
    if not proposal.get("contacts"):
        persisted_sender = _v19a_metadata_sender_contact(
            out.get("source_metadata")
        )
        if persisted_sender:
            proposal["contacts"] = [persisted_sender]
            proposal["sender_contact_fallback_used"] = True
            proposal["sender_contact_is_owner"] = False

    # Foundation 1.9: only when the message itself has no contact,
    # recover the actual WhatsApp sender as SOURCE_CONTACT.
    proposal = _v19_merge_sender_fallback(
        engine,
        proposal,
        str(out.get("source_table") or ""),
        str(out.get("source_raw_text") or ""),
        out.get("source_metadata"),
    )

    if not proposal.get("contacts"):
        proposal = _v19g_live_upstream_sender_contact(engine, proposal, out)

    out["lineage_metadata"] = lineage
    out["proposal"] = proposal
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


# FOUNDATION_1_9Q2_TYPED_AUDIT_NOTE
# FOUNDATION_1_9Q3_SOURCE_AUDIT_SQL_FIX
# FOUNDATION_1_9Q_SOURCE_LEVEL_PIN_REBUILD
def _rebuild_pin_source_spans_legacy(engine, source_message_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(source_message_id or "").strip()
    labeler_id = str((payload or {}).get("labeler_id") or "").strip()
    reason = str((payload or {}).get("reason") or "").strip() or "Source-level pin boundary repair 1.9Q"
    if not sid:
        raise HTTPException(400, "source_message_id is required")
    if not labeler_id:
        raise HTTPException(400, "labeler_id is required")

    with engine.begin() as conn:
        source_row = conn.execute(text("""
            SELECT source_message_id, raw_text, source_table, sampling_bucket
            FROM alliance_gold_source_messages
            WHERE source_message_id=:sid
            FOR UPDATE
        """), {"sid": sid}).mappings().first()
        if not source_row:
            raise HTTPException(404, "Gold source message not found")

        raw = str(source_row["raw_text"] or "")
        rebuilt = _v19m_pin_heading_split(raw)
        if not rebuilt or rebuilt.get("status") != "PASS":
            raise HTTPException(409, (rebuilt or {}).get("reason") or "No safe source-level pin reconstruction.")
        canonical = rebuilt.get("children") or []
        if len(canonical) < 2:
            raise HTTPException(409, "Source rebuild produced fewer than two canonical children")

        previous_end = -1
        canonical_by_text: Dict[str, List[Dict[str, Any]]] = {}
        for child in canonical:
            ctext = str(child.get("text") or "")
            start = int(child.get("start_offset") or 0)
            end = int(child.get("end_offset") or 0)
            if not ctext or raw[start:end] != ctext:
                raise HTTPException(409, "Canonical child is not an exact substring of original source")
            if start < previous_end:
                raise HTTPException(409, "Canonical source children overlap")
            previous_end = end
            canonical_by_text.setdefault(ctext, []).append(child)

        active_rows = conn.execute(text("""
            SELECT sp.span_id, sp.span_order,
                   sp.proposed_start_offset, sp.proposed_end_offset,
                   COALESCE(sp.human_text, sp.proposed_text) AS span_text,
                   sp.boundary_status,
                   EXISTS(
                       SELECT 1 FROM alliance_gold_span_labels l
                       WHERE l.span_id=sp.span_id AND l.active=TRUE
                   ) AS has_active_label
            FROM alliance_gold_spans sp
            WHERE sp.source_message_id=:sid
              AND COALESCE(sp.span_status, 'ACTIVE')='ACTIVE'
            ORDER BY sp.span_order, sp.created_at, sp.span_id
            FOR UPDATE
        """), {"sid": sid}).mappings().all()

        preserved_by_text: Dict[str, str] = {}
        preserved_ids: List[str] = []
        supersede_ids: List[str] = []
        invalidated_label_ids: List[str] = []

        for row in active_rows:
            span_id = str(row["span_id"])
            span_text = str(row["span_text"] or "").strip()
            canonical_matches = canonical_by_text.get(span_text) or []
            is_exact_canonical = len(canonical_matches) == 1
            if bool(row["has_active_label"]) and is_exact_canonical and span_text not in preserved_by_text:
                preserved_by_text[span_text] = span_id
                preserved_ids.append(span_id)
            else:
                supersede_ids.append(span_id)
                if bool(row["has_active_label"]):
                    invalidated_label_ids.append(span_id)

        if invalidated_label_ids:
            conn.execute(text("""
                UPDATE alliance_gold_span_labels
                SET active=FALSE,
                    notes=concat_ws(E'\\n', NULLIF(notes,''), CAST(:audit_note AS text)),
                    updated_at=now()
                WHERE active=TRUE
                  AND span_id = ANY(CAST(:ids AS uuid[]))
            """), {
                "ids": invalidated_label_ids,
                "audit_note": (
                    "[Foundation 1.9Q source-boundary correction by " + labeler_id +
                    "] Label invalidated because its evidence span was not an exact "
                    "canonical child of the original source. " + reason
                ),
            })

        if supersede_ids:
            _deactivate_lineage_dependencies(conn, supersede_ids)
            conn.execute(text("""
                UPDATE alliance_gold_spans
                SET span_status='SUPERSEDED',
                    boundary_status='SUPERSEDED',
                    boundary_action='SOURCE_REBUILD',
                    superseded_at=now(),
                    lineage_metadata=COALESCE(lineage_metadata, '{}'::jsonb)
                        || CAST(:meta AS jsonb),
                    updated_at=now()
                WHERE span_id = ANY(CAST(:ids AS uuid[]))
            """), {
                "ids": supersede_ids,
                "meta": _json({
                    "operation": "SOURCE_LEVEL_PIN_REBUILD_1_9Q",
                    "labeler_id": labeler_id,
                    "reason": reason,
                    "source_message_id": sid,
                }),
            })

        # FOUNDATION_1_9Q5_SHIFT_ALL_SOURCE_ROWS
        # The unique key is enforced across ACTIVE and SUPERSEDED rows.
        # Move every historical row for this source out of canonical 1..N
        # before restoring preserved spans / creating canonical children.
        current_max_order = int(conn.execute(text("""
            SELECT COALESCE(MAX(span_order), 0)
            FROM alliance_gold_spans
            WHERE source_message_id=:sid
        """), {"sid": sid}).scalar() or 0)
        safe_order_shift = current_max_order + 1000000

        conn.execute(text("""
            UPDATE alliance_gold_spans
            SET span_order=span_order+:safe_shift,
                updated_at=now()
            WHERE source_message_id=:sid
        """), {"sid": sid, "safe_shift": safe_order_shift})

        final_ids: List[str] = []
        created_ids: List[str] = []

        for order_no, child in enumerate(canonical, start=1):
            ctext = str(child["text"])
            start = int(child["start_offset"])
            end = int(child["end_offset"])
            preserved_id = preserved_by_text.get(ctext)

            if preserved_id:
                conn.execute(text("""
                    UPDATE alliance_gold_spans
                    SET span_order=:span_order,
                        proposed_start_offset=:start_offset,
                        proposed_end_offset=:end_offset,
                        updated_at=now(),
                        lineage_metadata=COALESCE(lineage_metadata, '{}'::jsonb)
                          || CAST(:meta AS jsonb)
                    WHERE span_id=:span_id
                """), {
                    "span_id": preserved_id,
                    "span_order": order_no,
                    "start_offset": start,
                    "end_offset": end,
                    "meta": _json({
                        "source_rebuild_verified": True,
                        "source_rebuild_version": "1.9Q",
                        "canonical_boundary_strategy": "PIN_HEADING_OWNS_FOLLOWING_FACTS_1_9P",
                    }),
                })
                final_ids.append(preserved_id)
                continue

            new_id = str(uuid.uuid4())
            proposal = child.get("proposal") if isinstance(child.get("proposal"), dict) else {}
            conn.execute(text("""
                INSERT INTO alliance_gold_spans (
                    span_id, source_message_id, span_order,
                    proposed_start_offset, proposed_end_offset,
                    proposed_text, proposal_method, proposal_confidence,
                    parent_span_id, span_status, boundary_status, lineage_metadata
                ) VALUES (
                    :span_id, :source_message_id, :span_order,
                    :start_offset, :end_offset,
                    :proposed_text, 'SOURCE_LEVEL_PIN_REBUILD_1_9Q', 1.0,
                    NULL, 'ACTIVE', 'PENDING', CAST(:lineage_metadata AS jsonb)
                )
            """), {
                "span_id": new_id,
                "source_message_id": sid,
                "span_order": order_no,
                "start_offset": start,
                "end_offset": end,
                "proposed_text": ctext,
                "lineage_metadata": _json({
                    "created_by": labeler_id,
                    "operation": "SOURCE_LEVEL_PIN_REBUILD_1_9Q",
                    "reason": reason,
                    "source_grounded_context": child.get("context") or {},
                    "preview_proposal_context": {
                        k: v for k, v in proposal.items()
                        if k in {"locality_hint","city_hint","project_name_hint","context_provenance","entity_scope"}
                    },
                }),
            })
            final_ids.append(new_id)
            created_ids.append(new_id)

        if supersede_ids:
            conn.execute(text("""
                UPDATE alliance_gold_spans
                SET superseded_by=CAST(:replacement_ids AS jsonb),
                    updated_at=now()
                WHERE span_id = ANY(CAST(:ids AS uuid[]))
            """), {
                "ids": supersede_ids,
                "replacement_ids": _json(final_ids),
            })

        _refresh_source_after_boundary_edit(conn, sid)

    return _json_safe({
        "status": "SOURCE_REBUILT",
        "version": VERSION,
        "repair_version": "1.9Q",
        "source_message_id": sid,
        "canonical_child_count": len(canonical),
        "preserved_labeled_spans": len(preserved_ids),
        "created_pending_spans": len(created_ids),
        "superseded_malformed_spans": len(supersede_ids),
        "invalidated_malformed_gold_labels": len(invalidated_label_ids),
        "invalidated_span_ids": invalidated_label_ids,
        "shared_context": rebuilt.get("shared_context") or [],
        "academy_write_only": True,
        "production_writes": False,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    })


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
                WHERE COALESCE(sp.span_status, 'ACTIVE')='ACTIVE'
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
                WHERE COALESCE(sp.span_status, 'ACTIVE')='ACTIVE'
                  AND sp.boundary_status='LABELED'
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


# FOUNDATION_1_9S_GENERIC_SOURCE_REBUILD
def rebuild_pin_source_spans(engine, source_message_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Foundation 1.9S extends Repair Current Source to repeated sparkle headings.
    sid = str(source_message_id or "").strip()
    labeler_id = str((payload or {}).get("labeler_id") or "").strip()
    reason = str((payload or {}).get("reason") or "").strip() or "Source-level sparkle inventory rebuild 1.9S"

    if not sid:
        raise HTTPException(400, "source_message_id is required")
    if not labeler_id:
        raise HTTPException(400, "labeler_id is required")

    with engine.connect() as conn:
        source_row = conn.execute(
            text(
                "SELECT source_message_id, raw_text "
                "FROM alliance_gold_source_messages "
                "WHERE source_message_id=:sid"
            ),
            {"sid": sid},
        ).mappings().first()

    if not source_row:
        raise HTTPException(404, "Gold source message not found")

    raw = str(source_row.get("raw_text") or "")

    rebuilt = _v19r_sparkle_heading_split(raw)
    if not isinstance(rebuilt, dict) or rebuilt.get("status") != "PASS":
        rebuilt = _v19t_natural_commercial_heading_split(raw)

    if not isinstance(rebuilt, dict) or rebuilt.get("status") != "PASS":
        return _rebuild_pin_source_spans_legacy(engine, source_message_id, payload)

    raw_children = list(rebuilt.get("children") or [])
    if len(raw_children) < 2:
        return _rebuild_pin_source_spans_legacy(engine, source_message_id, payload)

    canonical = []
    for idx, child in enumerate(raw_children, start=1):
        ctext = str((child or {}).get("text") or "")
        start = int((child or {}).get("start_offset") or 0)
        end = int((child or {}).get("end_offset") or 0)

        if not ctext.strip() or end <= start:
            raise HTTPException(409, "Source rebuild produced an invalid canonical child span")
        if raw[start:end] != ctext:
            raise HTTPException(
                409,
                "Source rebuild grounding check failed: child is not exact source evidence",
            )

        canonical.append(
            {
                "span_order": idx,
                "text": ctext,
                "start_offset": start,
                "end_offset": end,
            }
        )

    previous_end = -1
    for child in canonical:
        if child["start_offset"] < previous_end:
            raise HTTPException(409, "Source rebuild produced overlapping canonical child spans")
        previous_end = child["end_offset"]

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT sp.span_id, sp.span_order, sp.proposed_start_offset, "
                "sp.proposed_end_offset, sp.proposed_text, sp.boundary_status, "
                "COALESCE(sp.span_status,'ACTIVE') AS span_status, "
                "EXISTS (SELECT 1 FROM alliance_gold_span_labels gl "
                "WHERE gl.span_id=sp.span_id AND gl.active=TRUE) AS has_active_gold "
                "FROM alliance_gold_spans sp "
                "WHERE sp.source_message_id=:sid "
                "ORDER BY sp.span_order, sp.created_at, sp.span_id"
            ),
            {"sid": sid},
        ).mappings().all()

        current_max_order = int(
            conn.execute(
                text(
                    "SELECT COALESCE(MAX(span_order),0) "
                    "FROM alliance_gold_spans WHERE source_message_id=:sid"
                ),
                {"sid": sid},
            ).scalar()
            or 0
        )
        safe_shift = current_max_order + 1000000

        conn.execute(
            text(
                "UPDATE alliance_gold_spans "
                "SET span_order=span_order+:safe_shift, updated_at=now() "
                "WHERE source_message_id=:sid"
            ),
            {"sid": sid, "safe_shift": safe_shift},
        )

        used_ids = set()
        canonical_ids = []
        reused = 0
        created = 0

        for child in canonical:
            exact_candidates = [
                row for row in existing
                if str(row.get("proposed_text") or "") == child["text"]
                and str(row.get("span_id")) not in used_ids
            ]
            exact_candidates.sort(
                key=lambda row: (
                    0 if bool(row.get("has_active_gold")) else 1,
                    0 if str(row.get("span_status") or "").upper() == "ACTIVE" else 1,
                    int(row.get("span_order") or 0),
                )
            )

            if exact_candidates:
                chosen = exact_candidates[0]
                span_id = str(chosen["span_id"])
                used_ids.add(span_id)
                canonical_ids.append(span_id)
                reused += 1

                conn.execute(
                    text(
                        "UPDATE alliance_gold_spans SET "
                        "span_order=:span_order, "
                        "proposed_start_offset=:start_offset, "
                        "proposed_end_offset=:end_offset, "
                        "proposed_text=:proposed_text, "
                        "proposal_method='SOURCE_REBUILD_GENERIC_1_9T', "
                        "proposal_confidence=1.0, "
                        "span_status='ACTIVE', "
                        "superseded_at=NULL, "
                        "superseded_by='[]'::jsonb, "
                        "boundary_status=CASE WHEN EXISTS ("
                        "SELECT 1 FROM alliance_gold_span_labels gl "
                        "WHERE gl.span_id=alliance_gold_spans.span_id AND gl.active=TRUE"
                        ") THEN 'LABELED' ELSE 'PENDING' END, "
                        "updated_at=now() WHERE span_id=:span_id"
                    ),
                    {
                        "span_order": child["span_order"],
                        "start_offset": child["start_offset"],
                        "end_offset": child["end_offset"],
                        "proposed_text": child["text"],
                        "span_id": span_id,
                    },
                )
            else:
                span_id = str(uuid.uuid4())
                canonical_ids.append(span_id)
                created += 1

                conn.execute(
                    text(
                        "INSERT INTO alliance_gold_spans ("
                        "span_id, source_message_id, span_order, "
                        "proposed_start_offset, proposed_end_offset, proposed_text, "
                        "proposal_method, proposal_confidence, boundary_status, "
                        "span_status, lineage_metadata"
                        ") VALUES ("
                        ":span_id, :sid, :span_order, :start_offset, :end_offset, "
                        ":proposed_text, 'SOURCE_REBUILD_GENERIC_1_9T', 1.0, "
                        "'PENDING', 'ACTIVE', CAST(:lineage_metadata AS jsonb))"
                    ),
                    {
                        "span_id": span_id,
                        "sid": sid,
                        "span_order": child["span_order"],
                        "start_offset": child["start_offset"],
                        "end_offset": child["end_offset"],
                        "proposed_text": child["text"],
                        "lineage_metadata": _json(
                            {
                                "repair": "FOUNDATION_1_9T_GENERIC_SOURCE_REBUILD",
                                "boundary_strategy": rebuilt.get("boundary_strategy"),
                                "shared_context_preserved": bool(rebuilt.get("shared_context")),
                            }
                        ),
                    },
                )

        canonical_id_set = set(canonical_ids)
        obsolete_ids = [
            str(row["span_id"])
            for row in existing
            if str(row["span_id"]) not in canonical_id_set
        ]

        invalidated_labels = 0
        if obsolete_ids:
            id_params = {f"oid_{i}": value for i, value in enumerate(obsolete_ids)}
            id_sql = ",".join(f":oid_{i}" for i in range(len(obsolete_ids)))

            invalidated_labels = int(
                conn.execute(
                    text(
                        f"SELECT count(*) FROM alliance_gold_span_labels "
                        f"WHERE active=TRUE AND span_id IN ({id_sql})"
                    ),
                    id_params,
                ).scalar()
                or 0
            )

            conn.execute(
                text(
                    f"UPDATE alliance_gold_span_labels SET active=FALSE, "
                    f"notes=CASE WHEN COALESCE(notes,'')='' "
                    f"THEN CAST(:audit_note AS text) "
                    f"ELSE notes || E'\\n' || CAST(:audit_note AS text) END, "
                    f"updated_at=now() "
                    f"WHERE active=TRUE AND span_id IN ({id_sql})"
                ),
                {
                    **id_params,
                    "audit_note": (
                        "[INVALIDATED_BY_FOUNDATION_1_9T] "
                        + reason
                        + ". Original source evidence preserved."
                    ),
                },
            )

            conn.execute(
                text(
                    f"UPDATE alliance_gold_relationship_labels SET active=FALSE "
                    f"WHERE active=TRUE AND ("
                    f"left_span_id IN ({id_sql}) OR right_span_id IN ({id_sql}))"
                ),
                id_params,
            )

            conn.execute(
                text(
                    f"UPDATE alliance_gold_spans SET "
                    f"span_status='SUPERSEDED', superseded_at=now(), "
                    f"superseded_by=CAST(:replacement_ids AS jsonb), updated_at=now() "
                    f"WHERE span_id IN ({id_sql})"
                ),
                {
                    **id_params,
                    "replacement_ids": _json(canonical_ids),
                },
            )

        pending = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM alliance_gold_spans "
                    "WHERE source_message_id=:sid "
                    "AND COALESCE(span_status,'ACTIVE')='ACTIVE' "
                    "AND boundary_status <> 'LABELED'"
                ),
                {"sid": sid},
            ).scalar()
            or 0
        )

        conn.execute(
            text(
                "UPDATE alliance_gold_source_messages SET "
                "proposed_span_count=:n, "
                "labeling_status=CASE WHEN :pending=0 THEN 'LABELED' "
                "ELSE 'IN_PROGRESS' END, updated_at=now() "
                "WHERE source_message_id=:sid"
            ),
            {"n": len(canonical), "pending": pending, "sid": sid},
        )

    return {
        "status": "SOURCE_REBUILT",
        "repair_version": "1.9T",
        "boundary_strategy": rebuilt.get("boundary_strategy"),
        "canonical_span_count": len(canonical),
        "reused_existing_spans": reused,
        "created_spans": created,
        "superseded_spans": len(obsolete_ids),
        "invalidated_conflicting_gold_labels": invalidated_labels,
        "shared_context_preserved": bool(rebuilt.get("shared_context")),
        "academy_writes_only": True,
        "production_tables_modified": [],
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }



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

<div class="actions">
<button class="secondary" type="button" onclick="prepareAtomicSplit()">Prepare Atomic Split</button>
<button class="secondary" type="button" onclick="mergeWithNext()">Merge With Next Span</button>
<button class="secondary" type="button" onclick="repairCurrentSource()">Repair Current Source</button>
</div>
<div id="splitPanel" style="display:none;margin-top:12px;border:1px solid #f59e0b;border-radius:8px;padding:12px;background:#fffbeb">
<strong>Atomic Split Editor</strong>
<div class="small" style="margin-top:4px">Each child must be an exact cut from the original evidence. Shared broker/contact context is preserved in lineage, not copied into every property.</div>
<div id="splitChildren"></div>
<div id="splitContext" class="small" style="margin-top:8px"></div>
<div class="actions">
<button class="primary" type="button" onclick="confirmAtomicSplit()">Confirm Real Split</button>
<button class="secondary" type="button" onclick="cancelAtomicSplit()">Cancel Split</button>
</div>
</div>

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
<option>INVENTORY_GROUP</option>
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

<label>Property fields JSON</label>
<textarea id="propertyFields">{}</textarea>

<label>Requirement fields JSON</label>
<textarea id="requirementFields">{}</textarea>

<label>Notes / why</label>
<textarea id="notes"></textarea>

<div class="actions">
<button class="good" onclick="quickSave('PROPERTY_AVAILABILITY')">Correct Property</button>
<button class="good" onclick="quickSave('INVENTORY_GROUP')">Correct Inventory Group</button>
<button class="good" onclick="quickSave('REQUIREMENT')">Correct Requirement</button>
<button class="secondary" onclick="quickSave('PROJECT_HEADER')">Project Header</button>
<button class="secondary" onclick="quickSave('LOCALITY_HEADER')">Locality Header</button>
<button class="secondary" onclick="quickSave('FRAGMENT')">Fragment</button>
<button class="secondary" onclick="quickSave('NOISE')">Noise</button>
</div>
<div class="actions">
<button class="primary" onclick="save()">Save Edited Gold Label</button>
<button class="secondary" onclick="skipNext()">Skip / Next</button>
</div>
<div id="msg"></div>
</div>
</div>
</div>

<script>
let current=null;
let splitDraft=[];
let skippedSpanIds=[];

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
    const skipQuery = skippedSpanIds.length
      ? `?skip_span_ids=${encodeURIComponent(skippedSpanIds.join(","))}`
      : "";
    const r=await fetch("/api/property-brain-foundation/next-span"+skipQuery);
    const raw=await r.text();
    let d={};
    try{ d=JSON.parse(raw); }
    catch(e){ throw new Error("Backend returned non-JSON response"); }
    if(!r.ok) throw new Error(d.detail||d.message||"Gold Lab backend error");
    if(d.status!=="PASS"){
      document.getElementById("source").innerText=d.message||"No spans.";
      document.getElementById("span").innerText="";
      if(skippedSpanIds.length){
        document.getElementById("msg").innerText =
          "All currently-unlabeled spans have been skipped in this browser session. Refresh the page to revisit skipped spans.";
      }
      current=null;
      return;
    }
  current=d.span;
  splitDraft=[];
  document.getElementById("splitPanel").style.display="none";
  document.getElementById("splitChildren").innerHTML="";
  document.getElementById("source").innerText=current.source_raw_text;
  document.getElementById("span").innerText=current.proposed_text;
  document.getElementById("meta").innerText =
    `Source: ${current.source_table} | Bucket: ${current.sampling_bucket} | Length: ${current.message_length}`;
  const p=current.proposal||{};
  const senderStatus=p.sender_lineage_status||"NOT_CHECKED";
  const senderStage=p.sender_lineage_resolution_stage||"";
  const contactCount=(p.contacts||[]).length;
  document.getElementById("spanMeta").innerText =
    `Span ${current.span_order} | Span ID: ${current.span_id} | Source Message ID: ${current.source_message_id} | Proposal confidence: ${current.proposal_confidence} | Sender lineage: ${senderStatus}${senderStage ? " / "+senderStage : ""} | Contacts: ${contactCount}`;
  document.getElementById("contentType").value =
    ["PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT","FRAGMENT"].includes(p.content_type_hint)
      ? p.content_type_hint : "FRAGMENT";
  document.getElementById("transaction").value=p.transaction_type_hint||"UNKNOWN";
  document.getElementById("city").value=p.city_hint||"";
  document.getElementById("locality").value=p.locality_hint||"";
  document.getElementById("project").value=p.project_name_hint||"";
  document.getElementById("unit").value=p.unit_identifier_hint||"";
  document.getElementById("locations").value=(p.acceptable_locations||[]).join(", ");
  document.getElementById("uses").value=(p.suitable_uses||[]).join(", ");
  document.getElementById("areas").value=JSON.stringify(p.areas||[],null,2);
  document.getElementById("money").value=JSON.stringify(p.money_mentions||[],null,2);
  document.getElementById("contacts").value=JSON.stringify(p.contacts||[],null,2);
  document.getElementById("propertyFields").value=JSON.stringify(p.property_fields||{},null,2);
  document.getElementById("requirementFields").value=JSON.stringify(p.requirement_fields||{},null,2);
  }catch(e){
    current=null;
    document.getElementById("source").innerText="Gold Lab runtime error. Do not label this record.";
    document.getElementById("span").innerText="";
    document.getElementById("msg").innerText="ERROR: "+e.message;
  }
}

async function repairCurrentSource(){
  try{
    if(!current) throw new Error("No span loaded");
    const labeler=document.getElementById("labeler").value.trim();
    if(!labeler) throw new Error("Enter Labeler ID / team member name");
    const ok=confirm(
      "Repair this entire source from the ORIGINAL source message?\\n\\n"+
      "Exact already-labeled canonical property spans will be preserved. "+
      "Malformed/conflicting Gold labels will be invalidated with an audit trail. "+
      "Production inventory will NOT be written."
    );
    if(!ok) return;
    document.getElementById("msg").innerText="Repairing current source...";
    const r=await fetch(
      `/api/property-brain-foundation/source/${current.source_message_id}/rebuild-pin-spans`,
      {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({labeler_id:labeler,reason:"Human-confirmed source-level repair from Gold Lab"})
      }
    );
    const raw=await r.text();
    let d={};
    try{ d=JSON.parse(raw); }catch(e){ throw new Error("Backend returned non-JSON response"); }
    if(!r.ok) throw new Error(d.detail||d.message||"Source repair failed");
    document.getElementById("msg").innerText =
      `Source repaired: ${d.canonical_child_count} canonical properties; `+
      `${d.preserved_labeled_spans} labeled preserved; `+
      `${d.created_pending_spans} pending created; `+
      `${d.invalidated_malformed_gold_labels} malformed Gold label(s) invalidated.`;
    skippedSpanIds=[];
    await refreshProgress();
    await loadNext();
  }catch(e){
    document.getElementById("msg").innerText="ERROR: "+e.message;
  }
}

async function prepareAtomicSplit(){
  try{
    if(!current) throw new Error("No span loaded");
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/split-preview`);
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||d.message||"Split preview failed");
    if(d.status!=="PASS" || !d.children || d.children.length<2) throw new Error(d.reason||"No safe automatic atomic split found");
    document.getElementById("boundary").value="SPLIT";
    splitDraft=d.children.map(x=>({
      text:x.text,
      context:x.context||{},
      proposal:x.proposal||{}
    }));
    const host=document.getElementById("splitChildren");
    host.innerHTML="";
    splitDraft.forEach((child,i)=>{
      const label=document.createElement("label"); label.innerText=`Child ${i+1}`;
      const ta=document.createElement("textarea"); ta.className="splitChild"; ta.value=child.text; ta.dataset.childIndex=String(i); ta.style.minHeight="125px";
      host.appendChild(label); host.appendChild(ta);
    });
    const ctx=(d.shared_context||[]).filter(Boolean);
    document.getElementById("splitContext").innerText = ctx.length ? `Shared/context evidence preserved separately: ${ctx.join(" | ").slice(0,600)}` : "No shared context outside the child spans.";
    document.getElementById("splitPanel").style.display="block";
    document.getElementById("msg").innerText=`Prepared ${d.children.length} atomic child spans. Review them, then Confirm Real Split.`;
  }catch(e){ document.getElementById("msg").innerText="ERROR: "+e.message; }
}
function cancelAtomicSplit(){
  splitDraft=[]; document.getElementById("splitPanel").style.display="none"; document.getElementById("splitChildren").innerHTML="";
  if(document.getElementById("boundary").value==="SPLIT") document.getElementById("boundary").value="CORRECT";
}
async function confirmAtomicSplit(){
  try{
    if(!current) throw new Error("No span loaded");
    const labeler=document.getElementById("labeler").value.trim(); if(!labeler) throw new Error("Enter Labeler ID / team member name");
    const children=[...document.querySelectorAll(".splitChild")].map((x,i)=>({
      text:x.value.trim(),
      context:(splitDraft[i]&&splitDraft[i].context)||{},
      proposal:(splitDraft[i]&&splitDraft[i].proposal)||{}
    })).filter(x=>x.text);
    if(children.length<2) throw new Error("At least two child spans are required");
    const payload={labeler_id:labeler,children:children,reason:document.getElementById("notes").value.trim()||"Human atomic split in Gold Lab",invalidate_existing_labels:false};
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/split`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||JSON.stringify(d));
    document.getElementById("msg").innerText=`Real split complete: ${d.child_count} active child spans created. Parent preserved as SUPERSEDED.`;
    cancelAtomicSplit(); await refreshProgress(); await loadNext();
  }catch(e){ document.getElementById("msg").innerText="ERROR: "+e.message; }
}
async function mergeWithNext(){
  try{
    if(!current) throw new Error("No span loaded");
    const labeler=document.getElementById("labeler").value.trim(); if(!labeler) throw new Error("Enter Labeler ID / team member name");
    if(!window.confirm("Merge this ACTIVE span with the next ACTIVE span from the same source?")) return;
    const payload={labeler_id:labeler,reason:document.getElementById("notes").value.trim()||"Human atomic merge in Gold Lab",invalidate_existing_labels:false};
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/merge-next`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||JSON.stringify(d));
    document.getElementById("msg").innerText="Real merge complete. Original spans preserved as SUPERSEDED.";
    await refreshProgress(); await loadNext();
  }catch(e){ document.getElementById("msg").innerText="ERROR: "+e.message; }
}

async function skipNext(){
  try{
    if(!current) throw new Error("No span loaded");
    const id=String(current.span_id||"");
    if(id && !skippedSpanIds.includes(id)) skippedSpanIds.push(id);
    if(skippedSpanIds.length>500) skippedSpanIds=skippedSpanIds.slice(-500);
    await loadNext();
    if(current){
      document.getElementById("msg").innerText =
        "Skipped for this browser session. No Gold label was written.";
    }
  }catch(e){
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
    const boundaryAction=document.getElementById("boundary").value;
    if(boundaryAction==="SPLIT") throw new Error("Use Prepare Atomic Split → Confirm Real Split. SPLIT is no longer a cosmetic label.");
    if(boundaryAction==="MERGE") throw new Error("Use Merge With Next Span. MERGE is no longer a cosmetic label.");
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
      property_fields:parseJson("propertyFields"),
      requirement_fields:parseJson("requirementFields"),
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
    document.getElementById("propertyFields").value="{}";
    document.getElementById("requirementFields").value="{}";
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

    rent_block = """✨ DLH LEGACY
▪️ 3 BHK | 1250 Sq.ft.
▪️ Semi-Furnished
🅿️ 2 Car Parkings
💰 Rent: ₹3.00 Lakhs

✨ DLH LEGACY
▪️ 3 BHK | 1350 Sq.ft.
▪️ Fully Furnished
🅿️ 2 Car Parkings
💰 Rent: ₹4.00 Lakhs
"""
    rent_spans = propose_spans(rent_block)
    check(
        "TWO_DLH_PROPERTIES_SPLIT",
        len(rent_spans) == 2,
        [s["text"] for s in rent_spans],
    )
    check(
        "FIRST_RENT_STAYS_WITH_FIRST_PROPERTY",
        len(rent_spans) == 2 and "₹3.00 Lakhs" in rent_spans[0]["text"],
        [s["text"] for s in rent_spans],
    )
    check(
        "SECOND_RENT_STAYS_WITH_SECOND_PROPERTY",
        len(rent_spans) == 2 and "₹4.00 Lakhs" in rent_spans[1]["text"],
        [s["text"] for s in rent_spans],
    )
    check(
        "RENT_LINE_NOT_PROPERTY_ANCHOR",
        not _is_named_property_anchor("💰 Rent: ₹3.00 Lakhs")
        and _is_dependent_attribute_line("💰 Rent: ₹3.00 Lakhs"),
        {
            "anchor": _is_named_property_anchor("💰 Rent: ₹3.00 Lakhs"),
            "dependent": _is_dependent_attribute_line("💰 Rent: ₹3.00 Lakhs"),
        },
    )
    check(
        "PROJECT_HEADING_IS_PROPERTY_ANCHOR",
        _is_named_property_anchor("✨ DLH LEGACY"),
        _clean_anchor_text("✨ DLH LEGACY"),
    )

    check(
        "GOLD_UI_PROPERTY_FIELDS_PRESENT",
        'id="propertyFields"' in LAB_UI
        and "property_fields:parseJson" in LAB_UI,
        "Property fields JSON input + save mapping",
    )
    check(
        "GOLD_UI_REQUIREMENT_FIELDS_PRESENT",
        'id="requirementFields"' in LAB_UI
        and "requirement_fields:parseJson" in LAB_UI,
        "Requirement fields JSON input + save mapping",
    )
    check(
        "GOLD_SCHEMA_PROPERTY_FIELDS_PRESENT",
        "property_fields JSONB" in DDL,
        "alliance_gold_span_labels.property_fields",
    )
    check(
        "GOLD_SCHEMA_REQUIREMENT_FIELDS_PRESENT",
        "requirement_fields JSONB" in DDL,
        "alliance_gold_span_labels.requirement_fields",
    )


    mehran = """✨ MEHRAN
▪️ 3 BHK | 1250 Sq.ft.
▪️ Fully Furnished
🅿️ 1 Car Parking
💰 Rent: On Request"""
    mehran_p = propose_fields(mehran)
    check("GOLD_PROPOSAL_MEHRAN_PROJECT", mehran_p.get("project_name_hint") == "MEHRAN", mehran_p)
    check("GOLD_PROPOSAL_MEHRAN_TRANSACTION", mehran_p.get("transaction_type_hint") == "RENT", mehran_p)
    check(
        "GOLD_PROPOSAL_MEHRAN_PROPERTY_FIELDS",
        mehran_p.get("property_fields") == {
            "configuration": "3 BHK",
            "furnishing": "FULLY_FURNISHED",
            "parking_count": 1,
            "rent_on_request": True,
        },
        mehran_p.get("property_fields"),
    )
    check(
        "GOLD_PROPOSAL_GENERIC_AREA_REMAINS_UNKNOWN",
        len(mehran_p.get("areas") or []) == 1 and mehran_p["areas"][0].get("role") == "UNKNOWN",
        mehran_p.get("areas"),
    )

    acropolis = """✨ ACROPOLIS
▪️ 3 BHK | 1240 Sq.ft.
▪️ Semi-Furnished
🌿 Lower Floor | Garden Facing
🅿️ 1 Car Parking
▪️ Only Kitchen Cabinets & AC
💰 Rent: ₹2.50 Lakhs Negotiable
💰 Deposit: 3 Months
🔑 Immediate Possession
📋 One Day Notice with Profile"""
    acro_p = propose_fields(acropolis)
    acro_fields = acro_p.get("property_fields") or {}
    check(
        "GOLD_PROPOSAL_ACROPOLIS_PROPERTY_FIELDS",
        acro_fields.get("configuration") == "3 BHK"
        and acro_fields.get("furnishing") == "SEMI_FURNISHED"
        and acro_fields.get("floor_description") == "Lower Floor"
        and acro_fields.get("view") == "Garden Facing"
        and acro_fields.get("parking_count") == 1
        and acro_fields.get("negotiable") is True
        and acro_fields.get("possession") == "Immediate"
        and acro_fields.get("security_deposit_months") == 3,
        acro_fields,
    )
    check(
        "GOLD_PROPOSAL_ACROPOLIS_RENT_ROLE",
        any(
            x.get("role") == "TOTAL_RENT" and x.get("value") == 2.5 and x.get("unit") == "LAKH"
            for x in acro_p.get("money_mentions") or []
        ),
        acro_p.get("money_mentions"),
    )
    check(
        "GOLD_PROPOSAL_DEPOSIT_MONTHS_NO_RUPEE_INVENTION",
        any(
            x.get("role") == "SECURITY_DEPOSIT" and x.get("value") == 3 and x.get("unit") == "MONTHS"
            for x in acro_p.get("money_mentions") or []
        ),
        acro_p.get("money_mentions"),
    )

    khar = propose_fields("""✨ RUSTOMJEE PARAMOUNT – KHAR WEST
▪️ 3 BHK | 1365 Sq.ft.
▪️ Semi-Furnished
🅿️ 2 Car Parkings
💰 Rent: ₹3.50 Lakhs""")
    check(
        "GOLD_PROPOSAL_EXPLICIT_LOCALITY_SUFFIX",
        khar.get("project_name_hint") == "RUSTOMJEE PARAMOUNT"
        and khar.get("locality_hint") == "KHAR WEST"
        and khar.get("city_hint") is None,
        khar,
    )
    check(
        "GOLD_UI_PROJECT_PREFILL_PRESENT",
        'document.getElementById("project").value=p.project_name_hint||"";' in LAB_UI,
        "Project field loads from source-grounded proposal",
    )


    three_property_message = """*Builder Floor Available For Lease In DLF Phase 2*

Area - 316 Sqyds
Floor - Second Floor
Block - L Block
Rental - 1.10 Plus Maintenance
Facing - West Facing

Well maintain and walking distance from MG Road and Metro.

*For more details contact us:-*

*Builder Floor Available For Lease In Sector 56*

Size - 500 Sq.yds 4Bhk
Floor - First Floor
Facing - South Facing
Rental - Market Price

*Unused Floor With Two Car Parking Well Connected From Extension Road*

*Builder Floor Available For Lease In Anantraj Sector 63A*

Size - 179 Sq.yds
Floor - Third Floor (3Bhk)
Facing - North Facing
Rental - 60k Per Month
Semi Furnished With One Cover Car Parking

*Pictures On Call*

*Paramount Associates*
Hemant Lohia
9643582058"""
    atomic = automatic_atomic_split(three_property_message)
    check("ATOMIC_DLF_SECTOR56_ANANTRAJ_SPLIT_INTO_THREE", atomic.get("status") == "PASS" and len(atomic.get("children") or []) == 3 and "DLF Phase 2" in atomic["children"][0]["text"] and "Sector 56" in atomic["children"][1]["text"] and "Anantraj Sector 63A" in atomic["children"][2]["text"], atomic)
    check("ATOMIC_SHARED_BROKER_CONTEXT_NOT_COPIED", atomic.get("status") == "PASS" and all("9643582058" not in c["text"] for c in atomic["children"]) and any("9643582058" in x for x in atomic.get("shared_context") or []), atomic.get("shared_context"))
    check("ATOMIC_UI_REAL_SPLIT_PRESENT", "prepareAtomicSplit()" in LAB_UI and "confirmAtomicSplit()" in LAB_UI and "/split-preview" in LAB_UI, "Gold Lab real split controls")
    check("ATOMIC_LINEAGE_SCHEMA_PRESENT", any("span_status" in x for x in MIGRATIONS) and any("parent_span_id" in x for x in MIGRATIONS) and any("superseded_by" in x for x in MIGRATIONS), MIGRATIONS)

    check(
        "GOLD_UI_SKIP_NEXT_HAS_REAL_EXCLUSION",
        "skipNext()" in LAB_UI
        and "skippedSpanIds" in LAB_UI
        and "skip_span_ids=" in LAB_UI
        and 'onclick="skipNext()"' in LAB_UI,
        "Skip / Next excludes current span in-session without Gold write.",
    )
    check(
        "GOLD_SKIP_IS_NON_WRITING",
        "Skipped for this browser session. No Gold label was written." in LAB_UI,
        "Skip is navigation-only.",
    )
    check(
        "LIVE_WHATSAPP_SENDER_RECOVERY_AVAILABLE",
        callable(_v19g_live_upstream_sender_contact)
        and callable(span_contact_lineage_diagnostic),
        "Read-only live upstream sender recovery + diagnostic.",
    )
    check(
        "GOLD_SOURCE_COLUMN_DISCOVERY_PRESENT",
        "source_column_candidates" in resolve_upstream_sender_for_gold_source.__code__.co_varnames,
        "Sender lineage discovers raw_text/raw_message from actual source schema.",
    )
    check(
        "GOLD_UI_LINEAGE_VISIBLE",
        "Sender lineage:" in LAB_UI
        and "Span ID:" in LAB_UI
        and "Source Message ID:" in LAB_UI,
        "Gold Lab exposes span/source IDs and sender-lineage status.",
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
            "atomic_span_editor": True,
            "atomic_split_endpoint": "/api/property-brain-foundation/span/{span_id}/split",
            "boundary_engine": "PERSISTENT_SOURCE_GROUNDED_CONTEXT_V1_7A",
            "split_context_persistence": True,
            "inventory_group_supported": True,
            "range_expansion_policy": "NEVER_INVENT_INDIVIDUAL_PROPERTIES",
            "atomic_merge_endpoint": "/api/property-brain-foundation/span/{span_id}/merge-next",
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

    @app.post("/api/property-brain-foundation/sources/backfill-sender-lineage")
    def sources_backfill_sender_lineage(payload: Dict[str, Any] = Body(default={})):
        dry_run = bool((payload or {}).get("dry_run", True))
        return _json_response(backfill_sender_lineage(engine, dry_run=dry_run))

    @app.get("/api/property-brain-foundation/sources/upstream-sender-diagnostic")
    def sources_upstream_sender_diagnostic():
        return _json_response(upstream_sender_lineage_diagnostic(engine))

    @app.post("/api/property-brain-foundation/sources/backfill-upstream-sender")
    def sources_backfill_upstream_sender(payload: Dict[str, Any] = Body(default={})):
        dry_run = bool((payload or {}).get("dry_run", True))
        return _json_response(
            backfill_upstream_sender_lineage(engine, dry_run=dry_run)
        )

    @app.post("/api/property-brain-foundation/gold/repropose-unlabeled")
    def repropose_unlabeled_route():
        return _json_response(repropose_unlabeled_gold(engine))

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

    @app.post("/api/property-brain-foundation/proposal/preview")
    def proposal_preview_route(payload: Dict[str, Any] = Body(...)):
        raw = str(payload.get("text") or "").strip()
        if not raw:
            raise HTTPException(400, "text is required")
        if len(raw) > 20000:
            raise HTTPException(400, "text is too long for proposal preview")
        return _json_response({
            "status": "PASS",
            "version": VERSION,
            "mode": MODE,
            "proposal": propose_fields(raw),
            "academy_write_only": True,
            "production_writes": False,
        })

    @app.get("/api/property-brain-foundation/next-span")
    def next_span_route(
        labeler_id: Optional[str] = Query(None),
        skip_span_ids: Optional[str] = Query(None),
    ):
        return _json_response(
            next_span(engine, labeler_id, skip_span_ids=skip_span_ids)
        )

    @app.get("/api/property-brain-foundation/span/{span_id}/contact-lineage")
    def span_contact_lineage_route(span_id: str):
        return _json_response(span_contact_lineage_diagnostic(engine, span_id))

    @app.post("/api/property-brain-foundation/span/{span_id}/label")
    def label_span(span_id: str, payload: Dict[str, Any] = Body(...)):
        return _json_response(save_label(engine, span_id, payload))

    @app.get("/api/property-brain-foundation/span/{span_id}/split-preview")
    def split_span_preview_route(span_id: str):
        return _json_response(split_preview(engine, span_id))

    @app.post("/api/property-brain-foundation/span/{span_id}/split")
    def split_span_route(span_id: str, payload: Dict[str, Any] = Body(...)):
        return _json_response(split_span(engine, span_id, payload))

    @app.post("/api/property-brain-foundation/span/{span_id}/merge-next")
    def merge_span_next_route(span_id: str, payload: Dict[str, Any] = Body(...)):
        return _json_response(merge_with_next(engine, span_id, payload))

    @app.post("/api/property-brain-foundation/source/{source_message_id}/rebuild-pin-spans")
    def rebuild_pin_source_route(source_message_id: str, payload: Dict[str, Any] = Body(...)):
        return _json_response(rebuild_pin_source_spans(engine, source_message_id, payload))

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
