
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

VERSION = "1.5.0-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"
MODE = "CONTEXT_AWARE_ATOMIC_INVENTORY_BOUNDARY_ENGINE"

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
    lines = [x.strip() for x in (span_text or "").splitlines() if x.strip()]
    if not lines:
        return {"project_name": None, "locality": None}
    raw_title = lines[0]
    if not _is_named_property_anchor(raw_title):
        return {"project_name": None, "locality": None}
    title = _clean_anchor_text(raw_title)
    parts = re.split(r"\s+[–—-]\s+", title, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        if re.search(
            r"\b(?:WEST|EAST|NORTH|SOUTH|CENTRAL|SECTOR|PHASE|EXTENSION|EXTN)\b",
            right,
            re.I,
        ):
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

def automatic_atomic_split(text_value: str) -> Dict[str, Any]:
    # Foundation 1.5 deterministic atomic boundary engine.
    # Handles repeated explicit property headings and locality headers followed
    # by compact inventory bullets. Every child remains an exact ordered
    # substring of the parent evidence.
    raw = str(text_value or "")
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
                "proposal": propose_fields(child_text),
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
        proposal = propose_fields(child_text)
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
    children: List[str] = []
    for item in children_payload:
        value = item.get("text") if isinstance(item, dict) else item
        value = str(value or "").strip()
        if value:
            children.append(value)
    if len(children) < 2:
        raise HTTPException(400, "At least two non-empty child spans are required")
    located: List[Dict[str, Any]] = []
    cursor = 0
    ranges: List[Tuple[int, int]] = []
    for idx, child in enumerate(children, start=1):
        start = parent_text.find(child, cursor)
        if start < 0:
            raise HTTPException(400, f"Child {idx} is not an exact ordered substring of the parent span. Do not rewrite evidence while splitting; only cut the original text.")
        end = start + len(child)
        located.append({"child_order": idx, "start_offset": start, "end_offset": end, "text": child})
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
                "lineage_metadata": _json({"created_by": labeler_id, "operation": "SPLIT", "reason": reason, "parent_span_id": span_id}),
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
                SET active=FALSE, notes=concat_ws(E'\\n', NULLIF(notes,''), :audit_note), updated_at=now()
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
                WHERE COALESCE(sp.span_status, 'ACTIVE')='ACTIVE'
                  AND sp.boundary_status <> 'LABELED'
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
let splitDraft=[];

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
  splitDraft=[];
  document.getElementById("splitPanel").style.display="none";
  document.getElementById("splitChildren").innerHTML="";
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

async function prepareAtomicSplit(){
  try{
    if(!current) throw new Error("No span loaded");
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/split-preview`);
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||d.message||"Split preview failed");
    if(d.status!=="PASS" || !d.children || d.children.length<2) throw new Error(d.reason||"No safe automatic atomic split found");
    document.getElementById("boundary").value="SPLIT";
    splitDraft=d.children.map(x=>x.text);
    const host=document.getElementById("splitChildren");
    host.innerHTML="";
    splitDraft.forEach((txt,i)=>{
      const label=document.createElement("label"); label.innerText=`Child ${i+1}`;
      const ta=document.createElement("textarea"); ta.className="splitChild"; ta.value=txt; ta.style.minHeight="125px";
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
    const children=[...document.querySelectorAll(".splitChild")].map(x=>x.value.trim()).filter(Boolean);
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
            "boundary_engine": "CONTEXT_AWARE_ATOMIC_INVENTORY_V1_5",
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
    def next_span_route(labeler_id: Optional[str] = Query(None)):
        return _json_response(next_span(engine, labeler_id))

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
