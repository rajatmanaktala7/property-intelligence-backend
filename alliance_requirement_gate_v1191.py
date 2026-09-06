from __future__ import annotations

"""
Alliance CRE Requirement Gate
Complete replacement for alliance_requirement_gate_v1191.py

Safety invariants
-----------------
1. Historical recovery writes ONLY to pi_requirement_gate_v1191 staging.
2. This module NEVER inserts/updates/deletes Master Requirements.
3. matcher_eligible is derived from human status:
      VERIFIED ACTIVE -> True
      every other status -> False
4. Reprocessing may improve structured fields in staging, but it never
   downgrades/overwrites a human VERIFIED ACTIVE or REJECTED/EXPIRED decision.
"""

import hashlib
import html
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "11.9.7-TEAM-SAFE-COMPACT-GATE"
STATUSES = (
    "RAW",
    "AI-QUALIFIED",
    "NEEDS VERIFICATION",
    "VERIFIED ACTIVE",
    "REJECTED/EXPIRED",
)
HUMAN_LOCKED_STATUSES = {"VERIFIED ACTIVE", "REJECTED/EXPIRED"}

DDL = [
    """CREATE TABLE IF NOT EXISTS pi_requirement_gate_v1191(
        id BIGSERIAL PRIMARY KEY,
        evidence_key TEXT NOT NULL UNIQUE,
        source_type TEXT NOT NULL,
        source_table TEXT,
        source_pk TEXT,
        source_group TEXT,
        source_date TIMESTAMPTZ,
        original_message TEXT NOT NULL,
        message_hash TEXT NOT NULL,
        classification TEXT NOT NULL DEFAULT 'RAW',
        genuine_confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
        rejection_reason TEXT,
        transaction_type TEXT,
        property_category TEXT,
        intended_use TEXT,
        locations JSONB NOT NULL DEFAULT '[]'::jsonb,
        alternate_locations JSONB NOT NULL DEFAULT '[]'::jsonb,
        area_min_sqft NUMERIC,
        area_max_sqft NUMERIC,
        budget_min NUMERIC,
        budget_max NUMERIC,
        floor_requirement TEXT,
        frontage_requirement TEXT,
        parking_requirement TEXT,
        company_brand_person TEXT,
        contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
        extracted_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
        evidence_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
        duplicate_of TEXT,
        verified_by TEXT,
        verified_at TIMESTAMPTZ,
        verification_notes TEXT,
        expires_at TIMESTAMPTZ,
        matcher_eligible BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_req_gate_status
       ON pi_requirement_gate_v1191(classification,matcher_eligible)""",
    """CREATE INDEX IF NOT EXISTS idx_req_gate_hash
       ON pi_requirement_gate_v1191(message_hash)""",
    """CREATE TABLE IF NOT EXISTS pi_requirement_gate_audit_v1191(
        id BIGSERIAL PRIMARY KEY,
        gate_id BIGINT,
        action TEXT NOT NULL,
        actor TEXT,
        old_status TEXT,
        new_status TEXT,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
]

POSITIVE = (
    r"\bneed(?:ed|ing)?\b",
    r"\brequir(?:e|ed|ement|ements|ing)\b",
    r"\blooking\s+for\b",
    r"\bwanted\b",
    r"\bseeking\b",
    r"\bclient\s+(?:needs|requires|is\s+looking|looking)\b",
    r"\bspace\s+(?:needed|required)\b",
    r"\brequirement\s+for\b",
)

SUPPLY = (
    r"\bfor\s+sale\b",
    r"\bavailable\s+for\b",
    r"\bavailable\s+(?:on\s+)?rent\b",
    r"\bto\s+let\b",
    r"\bproperty\s+available\b",
    r"\binventory\s+available\b",
    r"\bwe\s+have\s+available\b",
)

NOISE = (
    r"^\s*(?:hi|hello|good morning|good evening|thanks|thank you|ok|okay)\W*$",
    r"\bsubscribe\b",
    r"\bfollow\s+us\b",
)

USE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bcloud\s*kitchen\b", "CLOUD KITCHEN"),
    (r"\bdark\s*kitchen\b", "CLOUD KITCHEN"),
    (r"\brestaurant\b", "RESTAURANT"),
    (r"\bcaf[eé]\b", "CAFE"),
    (r"\bbanquet(?:\s*hall)?\b", "BANQUET"),
    (r"\bfood\s*court\b", "FOOD COURT"),
    (r"\boffice\b", "OFFICE"),
    (r"\bco[-\s]?working\b", "COWORKING"),
    (r"\bretail\b", "RETAIL"),
    (r"\bshowroom\b", "SHOWROOM"),
    (r"\bwarehouse\b", "WAREHOUSE"),
    (r"\bgodown\b", "WAREHOUSE"),
    (r"\bhotel\b", "HOTEL"),
    (r"\blounge\b", "LOUNGE"),
    (r"\bnight\s*club\b", "CLUB"),
    (r"\bclub\b", "CLUB"),
    (r"\bguest\s*house\b", "GUEST HOUSE"),
    (r"\bindustrial\b", "INDUSTRIAL"),
    (r"\bfarm\s*house\b", "FARMHOUSE"),
    (r"\bfarmhouse\b", "FARMHOUSE"),
    (r"\bclinic\b", "CLINIC"),
    (r"\bhospital\b", "HOSPITAL"),
    (r"\bsalon\b", "SALON"),
    (r"\bgym\b", "GYM"),
    (r"\bspa\b", "SPA"),
    (r"\bschool\b", "SCHOOL"),
)

COMMERCIAL_USES = {
    "CLOUD KITCHEN", "RESTAURANT", "CAFE", "BANQUET", "FOOD COURT",
    "OFFICE", "COWORKING", "RETAIL", "SHOWROOM", "HOTEL", "LOUNGE",
    "CLUB", "GUEST HOUSE", "CLINIC", "HOSPITAL", "SALON", "GYM",
    "SPA", "SCHOOL",
}

# High-confidence regional phrases that are useful even when not present in
# the location alias seed file. These are direct textual extractions, not guesses.
REGION_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bsouth\s+delhi\b", "South Delhi"),
    (r"\bsouth\s+delhi\s+ncr\b", "South Delhi"),
    (r"\bdelhi\s+ncr\b", "Delhi NCR"),
    (r"\bnew\s+delhi\b", "New Delhi"),
    (r"\bcentral\s+delhi\b", "Central Delhi"),
    (r"\bnorth\s+delhi\b", "North Delhi"),
    (r"\bwest\s+delhi\b", "West Delhi"),
    (r"\beast\s+delhi\b", "East Delhi"),
    (r"\bnoida\b", "Noida"),
    (r"\bgreater\s+noida\b", "Greater Noida"),
    (r"\bgurugram\b", "Gurugram"),
    (r"\bgurgaon\b", "Gurugram"),
    (r"\bfaridabad\b", "Faridabad"),
    (r"\bghaziabad\b", "Ghaziabad"),
)

FLOOR_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\blower\s+ground(?:\s+floor)?\b|\blg\b", "Lower Ground Floor"),
    (r"\bupper\s+ground(?:\s+floor)?\b|\bug\b", "Upper Ground Floor"),
    (r"\bground\s+floor\b|\bgf\b", "Ground Floor"),
    (r"\bfirst\s+floor\b|\b1st\s+floor\b", "First Floor"),
    (r"\bsecond\s+floor\b|\b2nd\s+floor\b", "Second Floor"),
    (r"\bthird\s+floor\b|\b3rd\s+floor\b", "Third Floor"),
    (r"\bbasement\b", "Basement"),
)

TEXT_COLUMN_CANDIDATES = (
    "raw_message", "original_message", "message", "raw_text", "description",
    "requirement", "requirement_text", "details", "notes", "text", "content",
)
PK_COLUMN_CANDIDATES = ("id", "record_id", "requirement_id", "source_id")
DATE_COLUMN_CANDIDATES = (
    "source_date", "captured_at", "created_at", "entry_date", "date_captured"
)
GROUP_COLUMN_CANDIDATES = ("source_group", "group_name", "source_name", "source")


def _app(core):
    return getattr(core, "app", None) or core


def _engine(core):
    return getattr(core, "engine", None)


def _login(core, req):
    return getattr(core, "need_login", lambda r: "team")(req)


def _actor(core, req):
    return getattr(core, "actor_name", lambda r: "team")(req)


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _hash(s: str) -> str:
    return hashlib.sha256(_norm(s).lower().encode("utf-8")).hexdigest()


def _dedupe(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for v in values:
        x = _norm(v)
        if not x:
            continue
        key = x.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def _phones(s: str) -> List[str]:
    out: List[str] = []
    for x in re.findall(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)", s or ""):
        if x not in out:
            out.append(x)
    return out


def _phone_list_from_form(s: str) -> List[str]:
    found = _phones(s)
    if found:
        return found
    # Preserve user-entered contact text only when no valid Indian mobile was parsed.
    return _dedupe(re.split(r"[,;/\n]+", s or ""))


def _money_value(number: str, unit: str) -> float:
    x = float(str(number).replace(",", ""))
    u = (unit or "").lower()
    if u in ("cr", "crore", "crores"):
        return x * 10_000_000
    if u in ("l", "lac", "lacs", "lakh", "lakhs"):
        return x * 100_000
    if u == "k":
        return x * 1_000
    return x


def _to_float(v: Any) -> Optional[float]:
    s = _norm(v).replace(",", "")
    if not s:
        return None
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return None


def _extract_area(s: str) -> Tuple[Optional[float], Optional[float]]:
    unit = r"(?:sq\.?\s*ft|sqft|sft|square\s*feet)"
    # 800-1200 sqft / 800 to 1200 sq ft
    m = re.search(
        rf"(?i)\b(\d{{2,7}}(?:,\d{{3}})*)\s*(?:-|–|—|to)\s*"
        rf"(\d{{2,7}}(?:,\d{{3}})*)\s*{unit}\b",
        s,
    )
    if m:
        a, b = (
            float(m.group(1).replace(",", "")),
            float(m.group(2).replace(",", "")),
        )
        return (min(a, b), max(a, b))

    # area required 800-1200 (unit may appear before/after nearby)
    m = re.search(
        r"(?i)\b(?:area|space|size)\b.{0,30}?"
        r"(\d{2,7}(?:,\d{3})*)\s*(?:-|–|—|to)\s*(\d{2,7}(?:,\d{3})*)"
        r"(?:\s*(?:sq\.?\s*ft|sqft|sft|square\s*feet))?",
        s,
    )
    if m:
        a, b = (
            float(m.group(1).replace(",", "")),
            float(m.group(2).replace(",", "")),
        )
        return (min(a, b), max(a, b))

    m = re.search(rf"(?i)\b(\d{{2,7}}(?:,\d{{3}})*)\s*{unit}\b", s)
    if m:
        a = float(m.group(1).replace(",", ""))
        return (a, a)

    return (None, None)


def _extract_budget(s: str) -> Tuple[Optional[float], Optional[float]]:
    money_unit = r"(cr|crore|crores|l|lac|lacs|lakh|lakhs|k)"
    # Explicit monetary range where one or both units are written.
    m = re.search(
        rf"(?i)(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*{money_unit}?\s*"
        rf"(?:-|–|—|to)\s*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*{money_unit}\b",
        s,
    )
    if m:
        n1, u1, n2, u2 = m.group(1), m.group(2), m.group(3), m.group(4)
        u1 = u1 or u2
        a, b = _money_value(n1, u1), _money_value(n2, u2)
        return (min(a, b), max(a, b))

    vals: List[float] = []
    for n, u in re.findall(
        rf"(?i)(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*{money_unit}\b", s
    ):
        vals.append(_money_value(n, u))

    if len(vals) >= 2:
        return (min(vals), max(vals))
    if len(vals) == 1:
        # A single "budget/rent up to X" is treated as max only.
        return (None, vals[0])
    return (None, None)


def _extract_locations(s: str) -> List[str]:
    locations: List[str] = []

    # First, direct broad-region phrases.
    for pattern, canonical in REGION_PATTERNS:
        if re.search(pattern, s, re.I):
            locations.append(canonical)

    # Then use the project's curated alias seed when available.
    try:
        from property_brain.utils import load_json

        aliases = load_json("location_aliases_seed.json")
        if isinstance(aliases, dict):
            lower = s.casefold()
            # Longest aliases first to avoid a short alias masking a richer phrase.
            for alias, canonical in sorted(
                aliases.items(), key=lambda kv: len(str(kv[0])), reverse=True
            ):
                a = _norm(alias)
                c = _norm(canonical)
                if not a or not c:
                    continue
                # Word-ish boundary check avoids accidental substring matches.
                if re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", lower, re.I):
                    locations.append(c)
    except Exception:
        pass

    return _dedupe(locations)


def _extract_floor(s: str) -> Optional[str]:
    floors = [label for pattern, label in FLOOR_PATTERNS if re.search(pattern, s, re.I)]
    return " / ".join(_dedupe(floors)) if floors else None


def _extract_use(s: str) -> Optional[str]:
    for pattern, label in USE_PATTERNS:
        if re.search(pattern, s, re.I):
            return label
    return None


def _extract_transaction(s: str) -> Optional[str]:
    if re.search(r"\b(?:rent|rental|lease|leasing|take\s+on\s+rent|on\s+rent)\b", s, re.I):
        return "LEASE"
    if re.search(r"\b(?:buy|purchase|outright|acquire|acquisition)\b", s, re.I):
        return "PURCHASE"
    return None


def _property_category(s: str, intended_use: Optional[str]) -> Optional[str]:
    if re.search(r"\bcommercial\b", s, re.I) or intended_use in COMMERCIAL_USES:
        return "COMMERCIAL"
    if intended_use in {"WAREHOUSE", "INDUSTRIAL"}:
        return "INDUSTRIAL"
    if intended_use == "FARMHOUSE":
        return "FARMHOUSE"
    return None


def extract(raw: str) -> Dict[str, Any]:
    """
    Evidence-first requirement extraction.

    Missing facts remain None. The function never fabricates a locality,
    budget, transaction, contact or area.
    """
    s = _norm(raw)
    positive = sum(bool(re.search(p, s, re.I)) for p in POSITIVE)
    supply = sum(bool(re.search(p, s, re.I)) for p in SUPPLY)
    noise = sum(bool(re.search(p, s, re.I)) for p in NOISE)

    requirement_intent = "REQUIREMENT" if positive else None
    tx = _extract_transaction(s)
    intended_use = _extract_use(s)
    category = _property_category(s, intended_use)
    locations = _extract_locations(s)
    amin, amax = _extract_area(s)
    bmin, bmax = _extract_budget(s)
    floor = _extract_floor(s)
    phones = _phones(s)

    # Count actual extracted evidence, not the classification itself.
    detail_flags = (
        bool(tx),
        bool(intended_use),
        bool(category),
        bool(locations),
        amin is not None,
        bmax is not None,
        bool(floor),
        bool(phones),
    )
    details = sum(detail_flags)

    # Confidence is a staging aid only. It never activates matcher eligibility.
    conf = 0.12 + positive * 0.23 + min(details, 6) * 0.075
    conf -= supply * 0.28 + noise * 0.40
    conf = max(0.0, min(0.99, conf))

    if noise or (supply and not positive):
        status = "REJECTED/EXPIRED"
        reason = "noise_or_supply_side"
    elif positive and details >= 2:
        status = "AI-QUALIFIED"
        reason = None
    elif positive:
        status = "NEEDS VERIFICATION"
        reason = None
    else:
        status = "RAW"
        reason = None

    evidence_quality = (
        "STRONG" if positive and details >= 4
        else "PARTIAL" if positive and details >= 1
        else "UNKNOWN"
    )

    return {
        "classification": status,
        "requirement_intent": requirement_intent,
        "genuine_confidence": round(conf, 4),
        "rejection_reason": reason,
        "transaction_type": tx,
        "property_category": category,
        "intended_use": intended_use,
        "locations": locations,
        "alternate_locations": [],
        "area_min_sqft": amin,
        "area_max_sqft": amax,
        "budget_min": bmin,
        "budget_max": bmax,
        "floor_requirement": floor,
        "frontage_requirement": None,
        "parking_requirement": None,
        "company_brand_person": None,
        "contact_numbers": phones,
        "evidence_quality": evidence_quality,
    }


def candidate_tables(engine) -> List[Tuple[str, str, Optional[str], Optional[str], Optional[str]]]:
    out = []
    with engine.connect() as c:
        tables = c.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public'
              AND (table_name ILIKE '%require%' OR table_name ILIKE '%demand%')
            ORDER BY table_name
        """)).scalars().all()

        for t in tables:
            if t in (
                "pi_requirement_gate_v1191",
                "pi_requirement_gate_audit_v1191",
                "pi_master_requirements_v711",
            ):
                continue

            names = c.execute(
                text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t
                    ORDER BY ordinal_position
                """),
                {"t": t},
            ).scalars().all()

            tc = next((x for x in TEXT_COLUMN_CANDIDATES if x in names), None)
            if not tc:
                continue
            pk = next((x for x in PK_COLUMN_CANDIDATES if x in names), None)
            dc = next((x for x in DATE_COLUMN_CANDIDATES if x in names), None)
            gc = next((x for x in GROUP_COLUMN_CANDIDATES if x in names), None)
            out.append((t, tc, pk, dc, gc))
    return out


def _write_extraction_to_gate(
    conn,
    gid: int,
    original_message: str,
    existing_status: str,
    extraction: Dict[str, Any],
    action: str = "AUTO_REEXTRACT",
) -> None:
    """
    Update staging fields only.

    Human-locked decisions keep their classification. matcher_eligible remains
    strictly derived from the preserved/final status.
    """
    final_status = (
        existing_status if existing_status in HUMAN_LOCKED_STATUSES
        else extraction["classification"]
    )
    matcher = final_status == "VERIFIED ACTIVE"

    conn.execute(
        text("""
            UPDATE pi_requirement_gate_v1191
            SET classification=:classification,
                genuine_confidence=:confidence,
                rejection_reason=:rejection_reason,
                transaction_type=:transaction_type,
                property_category=:property_category,
                intended_use=:intended_use,
                locations=CAST(:locations AS JSONB),
                alternate_locations=CAST(:alternate_locations AS JSONB),
                area_min_sqft=:area_min_sqft,
                area_max_sqft=:area_max_sqft,
                budget_min=:budget_min,
                budget_max=:budget_max,
                floor_requirement=:floor_requirement,
                frontage_requirement=:frontage_requirement,
                parking_requirement=:parking_requirement,
                company_brand_person=:company_brand_person,
                contact_numbers=CAST(:contact_numbers AS JSONB),
                extracted_fields=CAST(:extracted_fields AS JSONB),
                evidence_quality=:evidence_quality,
                matcher_eligible=:matcher,
                message_hash=:message_hash,
                updated_at=NOW()
            WHERE id=:id
        """),
        {
            "id": gid,
            "classification": final_status,
            "confidence": extraction["genuine_confidence"],
            "rejection_reason": extraction["rejection_reason"],
            "transaction_type": extraction["transaction_type"],
            "property_category": extraction["property_category"],
            "intended_use": extraction["intended_use"],
            "locations": json.dumps(extraction["locations"]),
            "alternate_locations": json.dumps(extraction["alternate_locations"]),
            "area_min_sqft": extraction["area_min_sqft"],
            "area_max_sqft": extraction["area_max_sqft"],
            "budget_min": extraction["budget_min"],
            "budget_max": extraction["budget_max"],
            "floor_requirement": extraction["floor_requirement"],
            "frontage_requirement": extraction["frontage_requirement"],
            "parking_requirement": extraction["parking_requirement"],
            "company_brand_person": extraction["company_brand_person"],
            "contact_numbers": json.dumps(extraction["contact_numbers"]),
            "extracted_fields": json.dumps(extraction),
            "evidence_quality": extraction["evidence_quality"],
            "matcher": matcher,
            "message_hash": _hash(original_message),
        },
    )

    if existing_status != final_status or action != "AUTO_REEXTRACT":
        conn.execute(
            text("""
                INSERT INTO pi_requirement_gate_audit_v1191(
                    gate_id,action,actor,old_status,new_status,details
                )
                VALUES(
                    :id,:action,'SYSTEM',:old,:new,CAST(:details AS JSONB)
                )
            """),
            {
                "id": gid,
                "action": action,
                "old": existing_status,
                "new": final_status,
                "details": json.dumps({
                    "version": VERSION,
                    "structured_fields_repaired": True,
                    "master_requirements_mutated": False,
                }),
            },
        )


def reprocess_existing_gate(engine, limit: int = 25000) -> Dict[str, int]:
    stats = {"scanned": 0, "updated": 0, "human_status_preserved": 0}
    with engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT id, original_message, classification
                FROM pi_requirement_gate_v1191
                ORDER BY id
                LIMIT :n
            """),
            {"n": max(1, min(limit, 100000))},
        ).mappings().all()

    for r in rows:
        extraction = extract(r["original_message"])
        with engine.begin() as c:
            _write_extraction_to_gate(
                c,
                int(r["id"]),
                r["original_message"],
                r["classification"],
                extraction,
            )
        stats["scanned"] += 1
        stats["updated"] += 1
        if r["classification"] in HUMAN_LOCKED_STATUSES:
            stats["human_status_preserved"] += 1
    return stats


def recover(engine, limit_per_table: int = 5000) -> Dict[str, Any]:
    """
    Recover historical requirement evidence into staging only.

    Existing staging rows are re-extracted first, so earlier weak extraction
    is repaired even when the original source message is a duplicate.
    """
    stats: Dict[str, Any] = {
        "seen": 0,
        "inserted": 0,
        "duplicates": 0,
        "reprocessed": reprocess_existing_gate(engine),
        "tables": [],
    }

    for t, tc, pk, dc, gc in candidate_tables(engine):
        cols = [
            f'"{tc}"::text raw',
            f'"{pk}"::text pk' if pk else "NULL::text pk",
            f'"{dc}" dt' if dc else "NULL::timestamptz dt",
            f'"{gc}"::text grp' if gc else "NULL::text grp",
        ]

        try:
            with engine.connect() as c:
                rows = c.execute(
                    text(
                        f'SELECT {",".join(cols)} FROM "{t}" '
                        f'WHERE "{tc}" IS NOT NULL '
                        f'AND LENGTH(TRIM("{tc}"::text))>=8 LIMIT :n'
                    ),
                    {"n": max(1, min(limit_per_table, 25000))},
                ).mappings().all()
        except Exception as exc:
            stats["tables"].append(
                {"table": t, "status": "SKIPPED", "error": str(exc)[:180]}
            )
            continue

        ins = dup = 0
        for r in rows:
            raw = _norm(r["raw"])
            if not raw:
                continue
            h = _hash(raw)
            ev = f"{t}:{r['pk'] or h[:20]}"
            ex = extract(raw)
            stats["seen"] += 1

            with engine.begin() as c:
                existing = c.execute(
                    text("""
                        SELECT id
                        FROM pi_requirement_gate_v1191
                        WHERE message_hash=:h
                        LIMIT 1
                    """),
                    {"h": h},
                ).scalar()

                if existing:
                    dup += 1
                    stats["duplicates"] += 1
                    continue

                got = c.execute(
                    text("""
                        INSERT INTO pi_requirement_gate_v1191(
                            evidence_key,source_type,source_table,source_pk,
                            source_group,source_date,original_message,message_hash,
                            classification,genuine_confidence,rejection_reason,
                            transaction_type,property_category,intended_use,
                            locations,alternate_locations,
                            area_min_sqft,area_max_sqft,budget_min,budget_max,
                            floor_requirement,frontage_requirement,
                            parking_requirement,company_brand_person,
                            contact_numbers,extracted_fields,evidence_quality,
                            matcher_eligible
                        )
                        VALUES(
                            :ev,:st,:tb,:pk,:grp,:dt,:raw,:h,
                            :cl,:cf,:rr,:tx,:cat,:use,
                            CAST(:loc AS JSONB),CAST(:alt AS JSONB),
                            :amin,:amax,:bmin,:bmax,
                            :floor,:frontage,:parking,:company,
                            CAST(:phones AS JSONB),CAST(:fields AS JSONB),:eq,
                            FALSE
                        )
                        ON CONFLICT(evidence_key) DO NOTHING
                        RETURNING id
                    """),
                    {
                        "ev": ev,
                        "st": t.upper(),
                        "tb": t,
                        "pk": r["pk"],
                        "grp": r["grp"],
                        "dt": r["dt"],
                        "raw": raw,
                        "h": h,
                        "cl": ex["classification"],
                        "cf": ex["genuine_confidence"],
                        "rr": ex["rejection_reason"],
                        "tx": ex["transaction_type"],
                        "cat": ex["property_category"],
                        "use": ex["intended_use"],
                        "loc": json.dumps(ex["locations"]),
                        "alt": json.dumps(ex["alternate_locations"]),
                        "amin": ex["area_min_sqft"],
                        "amax": ex["area_max_sqft"],
                        "bmin": ex["budget_min"],
                        "bmax": ex["budget_max"],
                        "floor": ex["floor_requirement"],
                        "frontage": ex["frontage_requirement"],
                        "parking": ex["parking_requirement"],
                        "company": ex["company_brand_person"],
                        "phones": json.dumps(ex["contact_numbers"]),
                        "fields": json.dumps(ex),
                        "eq": ex["evidence_quality"],
                    },
                ).scalar()

                if got:
                    ins += 1
                    stats["inserted"] += 1

        stats["tables"].append(
            {
                "table": t,
                "status": "OK",
                "rows": len(rows),
                "inserted": ins,
                "duplicates": dup,
            }
        )
    return stats


def counts(engine) -> Dict[str, int]:
    d = {s: 0 for s in STATUSES}
    with engine.connect() as c:
        for s, n in c.execute(
            text("""
                SELECT classification,COUNT(*)
                FROM pi_requirement_gate_v1191
                GROUP BY classification
            """)
        ).all():
            d[s] = int(n)

        d["RAW EVIDENCE"] = int(
            c.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191")).scalar()
            or 0
        )
        d["MATCHER ELIGIBLE"] = int(
            c.execute(text("""
                SELECT COUNT(*)
                FROM pi_requirement_gate_v1191
                WHERE classification='VERIFIED ACTIVE'
                  AND matcher_eligible=TRUE
            """)).scalar()
            or 0
        )
    return d


def _json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x) for x in value if _norm(x)]
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if _norm(x)]
        except Exception:
            pass
        return _dedupe(re.split(r"[,;/]+", value))
    return [str(value)]


def _money_display(v: Any) -> str:
    n = _to_float(v)
    if n is None:
        return ""
    if n >= 10_000_000:
        return f"₹{n / 10_000_000:.2f} Cr"
    if n >= 100_000:
        return f"₹{n / 100_000:.2f} L"
    if n >= 1_000:
        return f"₹{n:,.0f}"
    return f"₹{n:g}"


def shell(body: str) -> str:
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Requirement Recovery Gate</title>
<style>
*{box-sizing:border-box}
body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f4f7fb;color:#172033}
header{background:#10223f;color:#fff;padding:18px 22px}
header b{font-size:20px}
header .sub{margin-top:5px;font-size:13px;opacity:.92}
nav,.wrap{padding:12px 18px}
nav{background:#fff;border-bottom:1px solid #d0d5dd}
nav a{display:inline-block;background:#10223f;color:#fff;padding:9px 12px;text-decoration:none;border-radius:6px;margin:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:8px;margin-bottom:12px}
.card{background:#fff;border:1px solid #d0d5dd;padding:12px;border-radius:9px}
.num{font-size:26px;font-weight:700;margin-top:4px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:end}
.toolbar form{display:flex;gap:7px;flex-wrap:wrap;align-items:end}
.safety{background:#fff8db;border:1px solid #e4c95c;padding:12px;border-radius:8px;margin-top:10px}
.info{background:#eef7ff;border:1px solid #9ec5e8;padding:10px;border-radius:8px;margin-top:10px}
input,select,textarea,button{font:inherit}
input,select,textarea{border:1px solid #98a2b3;border-radius:5px;padding:7px;background:#fff}
button{background:#10223f;color:#fff;border:0;border-radius:5px;padding:8px 11px;cursor:pointer}
button:hover{opacity:.9}
.tablebox{overflow:auto;max-height:72vh;background:#fff;border:1px solid #d0d5dd;border-radius:8px}
table{border-collapse:collapse;width:max-content;min-width:100%;font-size:12px}
th,td{border:1px solid #d0d5dd;padding:7px;vertical-align:top}
th{background:#e9eef5;position:sticky;top:0;z-index:2;white-space:nowrap}
.msg{min-width:360px;max-width:520px;white-space:normal;line-height:1.4}
.small{font-size:11px;color:#667085}
.badge{display:inline-block;padding:4px 7px;border-radius:999px;font-size:10px;font-weight:700;background:#eef2f6}
.matcher-yes{background:#dcfae6;color:#05603a}
.matcher-no{background:#f2f4f7;color:#475467}
.decision{min-width:330px}
.decision form{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.decision .wide{grid-column:1/-1}
.decision textarea{min-height:52px;resize:vertical}
.status-select{font-weight:700}
.verify-activate{font-weight:800;border:2px solid currentColor}

/* 11.9.7 compact team-safe gate */
.wrap{padding:8px 10px}
header{padding:10px 14px}
header b{font-size:16px}
header .sub{font-size:11px;margin-top:3px}
nav{padding:6px 10px}
nav a{padding:6px 9px;font-size:11px}
.grid{grid-template-columns:repeat(7,minmax(90px,1fr));gap:5px;margin-bottom:7px}
.card{padding:7px;border-radius:7px}
.grid .card b{font-size:9px}
.num{font-size:18px;margin-top:2px}
.toolbar{gap:5px}
.toolbar form{gap:4px}
.toolbar input,.toolbar select,.toolbar button{padding:5px 7px;font-size:10px}
.safety,.info{padding:7px;margin-top:6px;font-size:10px}
.tablebox{overflow:auto;max-height:68vh}
table.compact-table{width:100%;min-width:980px;table-layout:fixed;font-size:10px}
.compact-table th,.compact-table td{padding:4px 5px;vertical-align:middle;line-height:1.2}
.compact-table th{font-size:9px}
.compact-table td{overflow-wrap:anywhere}
.compact-table .c-id{width:34px;text-align:center}
.compact-table .c-status{width:92px}
.compact-table .c-req{width:220px}
.compact-table .c-location{width:105px}
.compact-table .c-area{width:78px}
.compact-table .c-floor{width:82px}
.compact-table .c-contact{width:92px}
.compact-table .c-ai{width:54px;text-align:center}
.compact-table .c-matcher{width:58px;text-align:center}
.compact-table .c-actions{width:230px}
.req-main{font-weight:700;font-size:10px}
.req-sub{font-size:9px;color:#667085;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row-actions{display:flex;gap:3px;align-items:center;flex-wrap:wrap}
.row-actions form{display:inline;margin:0}
.row-actions button{padding:4px 6px;font-size:9px;white-space:nowrap}
.btn-verify{background:#067647}
.btn-review{background:#b54708}
.btn-reject{background:#b42318}
.btn-edit{background:#344054;padding:4px 6px;border-radius:5px;color:#fff;cursor:pointer;font-size:9px;white-space:nowrap}
.edit-panel{margin-top:5px;padding:6px;background:#f8fafc;border:1px solid #d0d5dd;border-radius:6px}
.edit-panel form{display:grid;grid-template-columns:repeat(4,minmax(85px,1fr));gap:4px}
.edit-panel input,.edit-panel select,.edit-panel textarea{padding:4px;font-size:9px;min-width:0;width:100%}
.edit-panel textarea{grid-column:1/-1;min-height:38px}
.edit-panel .edit-wide{grid-column:span 2}
.edit-panel button{grid-column:1/-1;padding:5px;font-size:9px}
.status-badge{display:inline-block;padding:3px 5px;border-radius:999px;font-weight:700;font-size:8px;background:#eef2f6}
@media(max-width:1100px){
  table.compact-table{min-width:900px}
  .compact-table .c-req{width:190px}
  .compact-table .c-actions{width:215px}
}
@media(max-width:900px){
  .msg{min-width:280px}
  .decision{min-width:300px}
}
</style>
</head>
<body>
<header>
  <b>Alliance Requirement Recovery Gate · CRE """ + _e(VERSION) + """</b>
  <div class="sub">Extract → Review → Verify → Matcher. Only VERIFIED ACTIVE is matcher eligible.</div>
</header>
<nav>
  <a href="/alliance/primary">Dashboard</a>
  <a href="/alliance/requirements-gate">Requirement Gate</a>
  <a href="/alliance/primary/requirements">Master Requirements</a>
  <a href="/alliance/primary/matcher">Matcher</a>
</nav>
<div class="wrap">""" + body + "</div></body></html>"


def _status_options(selected: str) -> str:
    return "".join(
        f"<option value='{_e(s)}' {'selected' if s == selected else ''}>{_e(s)}</option>"
        for s in STATUSES
    )


def _decision_form(r: Dict[str, Any]) -> str:
    loc = ", ".join(_json_list(r.get("locations")))
    phones = ", ".join(_json_list(r.get("contact_numbers")))
    fields = r.get("extracted_fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    req_intent = fields.get("requirement_intent") or (
        "REQUIREMENT" if r.get("classification") != "REJECTED/EXPIRED" else ""
    )
    gid = int(r["id"])
    current_status = r.get("classification") or "RAW"

    return f"""
    <div class="row-actions">
      <form method="post" action="/alliance/requirements-gate/{gid}/quick-status">
        <input type="hidden" name="status" value="VERIFIED ACTIVE">
        <button class="btn-verify" type="submit"
                onclick="return confirm('Verify requirement {gid} and activate Matcher?');">✓ Verify</button>
      </form>
      <form method="post" action="/alliance/requirements-gate/{gid}/quick-status">
        <input type="hidden" name="status" value="NEEDS VERIFICATION">
        <button class="btn-review" type="submit">⚠ Review</button>
      </form>
      <form method="post" action="/alliance/requirements-gate/{gid}/quick-status">
        <input type="hidden" name="status" value="REJECTED/EXPIRED">
        <button class="btn-reject" type="submit"
                onclick="return confirm('Reject / expire requirement {gid}?');">✕ Reject</button>
      </form>
      <details>
        <summary class="btn-edit">✎ Edit</summary>
        <div class="edit-panel">
          <form method="post" action="/alliance/requirements-gate/{gid}/decision">
            <input type="hidden" name="status" value="{_e(current_status)}">
            <input name="requirement_intent" value="{_e(req_intent)}" placeholder="Intent">
            <select name="transaction_type">
              <option value="">Transaction unknown</option>
              <option value="LEASE" {'selected' if r.get('transaction_type')=='LEASE' else ''}>LEASE</option>
              <option value="PURCHASE" {'selected' if r.get('transaction_type')=='PURCHASE' else ''}>PURCHASE</option>
            </select>
            <input name="intended_use" value="{_e(r.get('intended_use'))}" placeholder="Use">
            <input name="location" value="{_e(loc)}" placeholder="Location">
            <input name="area_min_sqft" value="{_e(r.get('area_min_sqft'))}" placeholder="Area min">
            <input name="area_max_sqft" value="{_e(r.get('area_max_sqft'))}" placeholder="Area max">
            <input name="budget_max" value="{_e(r.get('budget_max'))}" placeholder="Budget max ₹">
            <input name="floor_requirement" value="{_e(r.get('floor_requirement'))}" placeholder="Floor">
            <input class="edit-wide" name="contact_numbers" value="{_e(phones)}" placeholder="Contact number(s)">
            <input class="edit-wide" name="verified_by" value="" placeholder="Edited / verified by">
            <textarea name="notes" placeholder="Notes">{_e(r.get('verification_notes'))}</textarea>
            <button type="submit">Save Details</button>
          </form>
        </div>
      </details>
    </div>
    """


def register(core):
    app = _app(core)
    engine = _engine(core)
    if engine is None:
        raise RuntimeError("Alliance Requirement Gate requires core.engine")

    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))

    @app.get("/api/cre1191/requirements/status")
    def status(req: Request):
        _login(core, req)
        return {
            "version": VERSION,
            "counts": counts(engine),
            "candidate_tables": [x[0] for x in candidate_tables(engine)],
            "master_requirements_mutation": False,
            "matcher_policy": "ONLY VERIFIED ACTIVE",
        }

    @app.get("/api/cre1191/requirements/extract-preview")
    def extract_preview(req: Request, message: str = Query(..., min_length=1)):
        _login(core, req)
        return {
            "version": VERSION,
            "message": message,
            "extraction": extract(message),
            "writes": False,
        }

    @app.post("/alliance/requirements-gate/recover")
    def do_recover(req: Request, limit_per_table: int = Form(5000)):
        _login(core, req)
        recover(engine, max(100, min(limit_per_table, 25000)))
        return RedirectResponse("/alliance/requirements-gate?recovered=1", 303)

    @app.post("/alliance/requirements-gate/reprocess")
    def do_reprocess(req: Request, limit: int = Form(25000)):
        _login(core, req)
        reprocess_existing_gate(engine, max(1, min(limit, 100000)))
        return RedirectResponse("/alliance/requirements-gate?reprocessed=1", 303)

    @app.post("/alliance/requirements-gate/{gid}/quick-status")
    def quick_status(
        req: Request,
        gid: int,
        status: str = Form(...),
    ):
        _login(core, req)

        allowed = {"VERIFIED ACTIVE", "NEEDS VERIFICATION", "REJECTED/EXPIRED"}
        if status not in allowed:
            raise HTTPException(400, "Invalid quick status")

        eligible = status == "VERIFIED ACTIVE"
        actor = _actor(core, req)

        with engine.begin() as c:
            old_row = c.execute(
                text("""
                    SELECT classification
                    FROM pi_requirement_gate_v1191
                    WHERE id=:id
                """),
                {"id": gid},
            ).mappings().first()
            if old_row is None:
                raise HTTPException(404, "Not found")

            c.execute(
                text("""
                    UPDATE pi_requirement_gate_v1191
                    SET classification=:status,
                        matcher_eligible=:eligible,
                        verified_by=CASE WHEN :eligible THEN :actor ELSE verified_by END,
                        verified_at=CASE WHEN :eligible THEN NOW() ELSE verified_at END,
                        updated_at=NOW()
                    WHERE id=:id
                """),
                {"id": gid, "status": status, "eligible": eligible, "actor": actor},
            )

            c.execute(
                text("""
                    INSERT INTO pi_requirement_gate_audit_v1191(
                        gate_id,action,actor,old_status,new_status,details
                    )
                    VALUES(
                        :id,'QUICK_STATUS',:actor,:old,:new,CAST(:details AS JSONB)
                    )
                """),
                {
                    "id": gid,
                    "actor": actor,
                    "old": old_row["classification"],
                    "new": status,
                    "details": json.dumps({
                        "matcher_eligible": eligible,
                        "structured_fields_edited": False,
                        "master_requirements_mutated": False,
                        "version": VERSION,
                    }),
                },
            )

        return RedirectResponse(
            f"/alliance/requirements-gate?quick_status={gid}:{status}", 303
        )

    @app.post("/alliance/requirements-gate/{gid}/decision")
    def decision(
        req: Request,
        gid: int,
        status: str = Form(...),
        quick_action: str = Form(""),
        requirement_intent: str = Form(""),
        transaction_type: str = Form(""),
        intended_use: str = Form(""),
        location: str = Form(""),
        area_min_sqft: str = Form(""),
        area_max_sqft: str = Form(""),
        budget_max: str = Form(""),
        floor_requirement: str = Form(""),
        contact_numbers: str = Form(""),
        verified_by: str = Form(""),
        notes: str = Form(""),
    ):
        _login(core, req)

        # Dedicated per-row verification action.
        if quick_action == "verify_activate":
            status = "VERIFIED ACTIVE"

        if status not in STATUSES:
            raise HTTPException(400, "Invalid status")

        tx = _norm(transaction_type).upper() or None
        if tx not in (None, "LEASE", "PURCHASE"):
            raise HTTPException(400, "Invalid transaction type")

        amin = _to_float(area_min_sqft)
        amax = _to_float(area_max_sqft)
        bmax = _to_float(budget_max)
        if amin is not None and amax is not None and amin > amax:
            amin, amax = amax, amin

        locations = _dedupe(re.split(r"[,;/\n]+", location or ""))
        phones = _phone_list_from_form(contact_numbers)
        actor = verified_by.strip() or _actor(core, req)
        eligible = status == "VERIFIED ACTIVE"

        with engine.begin() as c:
            old_row = c.execute(
                text("""
                    SELECT classification,extracted_fields
                    FROM pi_requirement_gate_v1191
                    WHERE id=:id
                """),
                {"id": gid},
            ).mappings().first()
            if old_row is None:
                raise HTTPException(404, "Not found")

            extracted_fields = old_row["extracted_fields"] or {}
            if not isinstance(extracted_fields, dict):
                extracted_fields = {}
            extracted_fields = dict(extracted_fields)
            extracted_fields.update({
                "requirement_intent": _norm(requirement_intent) or None,
                "transaction_type": tx,
                "intended_use": _norm(intended_use).upper() or None,
                "locations": locations,
                "area_min_sqft": amin,
                "area_max_sqft": amax,
                "budget_max": bmax,
                "floor_requirement": _norm(floor_requirement) or None,
                "contact_numbers": phones,
                "human_reviewed": True,
                "human_review_version": VERSION,
            })

            c.execute(
                text("""
                    UPDATE pi_requirement_gate_v1191
                    SET classification=:status,
                        matcher_eligible=:eligible,
                        transaction_type=:tx,
                        intended_use=:use,
                        property_category=CASE
                            WHEN CAST(:use AS TEXT) IS NOT NULL THEN COALESCE(property_category,'COMMERCIAL')
                            ELSE property_category
                        END,
                        locations=CAST(:locations AS JSONB),
                        area_min_sqft=:amin,
                        area_max_sqft=:amax,
                        budget_max=:bmax,
                        floor_requirement=:floor,
                        contact_numbers=CAST(:phones AS JSONB),
                        extracted_fields=CAST(:fields AS JSONB),
                        verified_by=CASE WHEN :eligible THEN :actor ELSE verified_by END,
                        verified_at=CASE WHEN :eligible THEN NOW() ELSE verified_at END,
                        verification_notes=:notes,
                        updated_at=NOW()
                    WHERE id=:id
                """),
                {
                    "id": gid,
                    "status": status,
                    "eligible": eligible,
                    "tx": tx,
                    "use": _norm(intended_use).upper() or None,
                    "locations": json.dumps(locations),
                    "amin": amin,
                    "amax": amax,
                    "bmax": bmax,
                    "floor": _norm(floor_requirement) or None,
                    "phones": json.dumps(phones),
                    "fields": json.dumps(extracted_fields),
                    "actor": actor,
                    "notes": notes,
                },
            )

            c.execute(
                text("""
                    INSERT INTO pi_requirement_gate_audit_v1191(
                        gate_id,action,actor,old_status,new_status,details
                    )
                    VALUES(
                        :id,'TEAM_DECISION',:actor,:old,:new,CAST(:details AS JSONB)
                    )
                """),
                {
                    "id": gid,
                    "actor": actor,
                    "old": old_row["classification"],
                    "new": status,
                    "details": json.dumps({
                        "notes": notes,
                        "structured_fields_edited": True,
                        "matcher_eligible": eligible,
                        "master_requirements_mutated": False,
                        "version": VERSION,
                    }),
                },
            )

        if quick_action == "verify_activate":
            return RedirectResponse(
                f"/alliance/requirements-gate?verified={gid}", 303
            )
        return RedirectResponse("/alliance/requirements-gate?decision_saved=1", 303)

    @app.get("/alliance/requirements-gate", response_class=HTMLResponse)
    def page(
        req: Request,
        status_filter: str = Query(""),
        q: str = Query(""),
        limit: int = Query(200, ge=1, le=1000),
        recovered: str = Query(""),
        reprocessed: str = Query(""),
        verified: str = Query(""),
        decision_saved: str = Query(""),
        quick_status: str = Query(""),
    ):
        _login(core, req)
        cs = counts(engine)
        cards = "".join(
            f"<div class='card'><b>{_e(k)}</b><div class='num'>{v:,}</div></div>"
            for k, v in cs.items()
        )

        wh = ["1=1"]
        p: Dict[str, Any] = {"n": limit}
        if status_filter:
            if status_filter not in STATUSES:
                raise HTTPException(400, "Invalid status filter")
            wh.append("classification=:s")
            p["s"] = status_filter
        if q:
            wh.append(
                "(original_message ILIKE :q "
                "OR COALESCE(source_group,'') ILIKE :q "
                "OR COALESCE(source_table,'') ILIKE :q "
                "OR COALESCE(intended_use,'') ILIKE :q "
                "OR COALESCE(floor_requirement,'') ILIKE :q)"
            )
            p["q"] = "%" + q + "%"

        with engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT * FROM pi_requirement_gate_v1191 WHERE "
                    + " AND ".join(wh)
                    + " ORDER BY updated_at DESC,id DESC LIMIT :n"
                ),
                p,
            ).mappings().all()

        filter_opts = "<option value=''>ALL STATUSES</option>" + "".join(
            f"<option value='{_e(s)}' {'selected' if status_filter == s else ''}>{_e(s)}</option>"
            for s in STATUSES
        )

        trs: List[str] = []
        for row in rows:
            r = dict(row)
            loc = ", ".join(_json_list(r.get("locations")))
            ph = ", ".join(_json_list(r.get("contact_numbers")))
            fields = r.get("extracted_fields") or {}
            if not isinstance(fields, dict):
                fields = {}
            req_intent = fields.get("requirement_intent") or ""
            confidence = float(r.get("genuine_confidence") or 0)
            matcher = bool(r.get("matcher_eligible"))

            tx = _norm(r.get("transaction_type"))
            use = _norm(r.get("intended_use"))
            req_title = " • ".join(x for x in (tx, use) if x) or req_intent or "Requirement"
            original = _norm(r.get("original_message"))
            original_short = original if len(original) <= 70 else original[:67] + "..."

            amin = r.get("area_min_sqft")
            amax = r.get("area_max_sqft")
            if amin is not None and amax is not None:
                area = f"{float(amin):g}–{float(amax):g}"
            elif amin is not None:
                area = f"{float(amin):g}"
            elif amax is not None:
                area = f"≤ {float(amax):g}"
            else:
                area = "—"

            matcher_html = (
                "<span class='badge matcher-yes'>YES</span>"
                if matcher else "<span class='badge matcher-no'>NO</span>"
            )
            status_html = f"<span class='status-badge'>{_e(r.get('classification'))}</span>"
            req_html = (
                f"<div class='req-main'>{_e(req_title)}</div>"
                f"<div class='req-sub' title='{_e(original)}'>{_e(original_short)}</div>"
            )

            trs.append(
                "<tr>"
                f"<td class='c-id'>{_e(r.get('id'))}</td>"
                f"<td class='c-status'>{status_html}</td>"
                f"<td class='c-req'>{req_html}</td>"
                f"<td class='c-location'>{_e(loc or '—')}</td>"
                f"<td class='c-area'>{_e(area)}</td>"
                f"<td class='c-floor'>{_e(r.get('floor_requirement') or '—')}</td>"
                f"<td class='c-contact'>{_e(ph or '—')}</td>"
                f"<td class='c-ai'>{confidence:.0%}</td>"
                f"<td class='c-matcher'>{matcher_html}</td>"
                f"<td class='c-actions'>{_decision_form(r)}</td>"
                "</tr>"
            )

        notice = ""
        if quick_status:
            notice = (
                "<div class='info'><b>Status updated.</b> Requirement "
                + _e(quick_status.replace(":", " → "))
                + ". Structured details were preserved.</div>"
            )
        elif verified:
            notice = (
                "<div class='info'><b>Verification saved.</b> Requirement ID "
                + _e(verified)
                + " is VERIFIED ACTIVE and Matcher eligibility has been activated.</div>"
            )
        elif decision_saved:
            notice = (
                "<div class='info'><b>Review decision saved.</b> "
                "The requirement staging record was updated successfully.</div>"
            )
        elif recovered:
            notice = (
                "<div class='info'><b>Recovery completed.</b> Existing staging rows "
                "were re-extracted first; historical source evidence was then recovered "
                "to the gate. Master Requirements was not mutated.</div>"
            )
        elif reprocessed:
            notice = (
                "<div class='info'><b>Staging reprocessing completed.</b> Structured "
                "fields were refreshed from original messages. Human final statuses "
                "were preserved.</div>"
            )

        body = f"""
        <div class="grid">{cards}</div>

        <div class="card">
          <div class="toolbar">
            <form method="get">
              <div>
                <div class="small">Status</div>
                <select name="status_filter">{filter_opts}</select>
              </div>
              <div>
                <div class="small">Search</div>
                <input name="q" value="{_e(q)}" placeholder="Message / source / use / floor">
              </div>
              <div>
                <div class="small">Rows</div>
                <input type="number" name="limit" value="{limit}" min="1" max="1000">
              </div>
              <button type="submit">Search</button>
            </form>

            <form method="post" action="/alliance/requirements-gate/reprocess">
              <input type="hidden" name="limit" value="25000">
              <button type="submit">Repair Existing Gate Rows</button>
            </form>

            <form method="post" action="/alliance/requirements-gate/recover">
              <input type="number" name="limit_per_table" value="5000" min="100" max="25000">
              <button type="submit">Recover Historical Requirements</button>
            </form>
          </div>

          <div class="safety">
            <b>Safety:</b> Recovery and reprocessing write only to Requirement Gate staging.
            They do not add historical records to Master Requirements. Matcher eligibility
            is derived automatically: only <b>VERIFIED ACTIVE</b> = YES.
          </div>
          {notice}
        </div>

        <div class="tablebox">
          <table class="compact-table">
            <thead>
              <tr>
                <th class="c-id">ID</th>
                <th class="c-status">Status</th>
                <th class="c-req">Requirement</th>
                <th class="c-location">Location</th>
                <th class="c-area">Area</th>
                <th class="c-floor">Floor</th>
                <th class="c-contact">Contact</th>
                <th class="c-ai">AI</th>
                <th class="c-matcher">Matcher</th>
                <th class="c-actions">Actions</th>
              </tr>
            </thead>
            <tbody>{''.join(trs)}</tbody>
          </table>
        </div>
        """

        return HTMLResponse(
            shell(body),
            headers={
                "Cache-Control": "no-store",
                "X-Alliance-CRE-Version": VERSION,
            },
        )

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "policy": "ONLY VERIFIED ACTIVE IS MATCHER ELIGIBLE",
        "candidate_tables": [x[0] for x in candidate_tables(engine)],
        "counts": counts(engine),
        "master_requirements_mutation": False,
    }
