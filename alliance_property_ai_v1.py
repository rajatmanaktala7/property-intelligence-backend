from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

VERSION = "2.0.0-PHASE2-PROPERTY-AI"

PROPERTY_SIGNALS = (
    "PLOT", "LAND", "VILLA", "APARTMENT", "FLAT", "OFFICE", "SHOP",
    "SHOWROOM", "RETAIL", "WAREHOUSE", "GODOWN", "FARMHOUSE",
    "RESTAURANT", "BANQUET", "COMMERCIAL", "BHK"
)

SALE_CONTEXT = (
    "OWNER WANTS", "OWNERS WANTS", "OWNER WANT", "OWNERS WANT",
    "ASKING", "DEMAND", "DEMANDING", "EXPECTING", "EXPECTED PRICE",
    "QUOTED AT", "QUOTE", "PRICE", "OUTRIGHT", "FOR SALE", "SALE",
    "SELL", "RESALE", "PURCHASE"
)

RENT_CONTEXT = (
    "FOR RENT", "RENT", "RENTAL", "LEASE", "LEASING",
    "TO LET", "MONTHLY RENT", "PER MONTH"
)

FEATURE_PATTERNS = {
    "tar_road": ("TAR ROAD", "TARRED ROAD"),
    "compounded": ("COMPOUNDED", "BOUNDARY WALL", "WALLED"),
    "corner": ("CORNER",),
    "main_road": ("MAIN ROAD", "MAIN ROAD FACING"),
    "parking": ("PARKING",),
    "power_backup": ("POWER BACKUP", "GENERATOR"),
    "lift": ("LIFT", "ELEVATOR"),
    "terrace": ("TERRACE",),
    "mou": ("HAS MOU", "MOU AVAILABLE", "MOU"),
}

USE_PATTERNS = {
    "SINGLE_LUXURY_VILLA": (
        "SINGLE LUXURY VILLA",
        "ONE LUXURY VILLA",
        "LUXURY VILLA"
    ),
    "RESTAURANT": ("RESTAURANT", "CAFE", "CAFÉ", "F&B"),
    "BANQUET": ("BANQUET", "WEDDING", "MARRIAGE"),
    "RETAIL": ("RETAIL", "SHOWROOM", "SHOP"),
    "OFFICE": ("OFFICE", "CORPORATE OFFICE"),
    "WAREHOUSE": ("WAREHOUSE", "GODOWN"),
    "HOSPITALITY": ("HOTEL", "RESORT", "GUEST HOUSE", "HOSPITALITY"),
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").upper()).strip()


def _evidence(raw: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, raw, re.I)
    return m.group(0).strip() if m else None


def _area(raw: str) -> Optional[Dict[str, Any]]:
    patterns = [
        (
            r"\b(\d{2,8}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft|square\s*feet|square\s*foot)\b",
            "sqft",
            1.0,
        ),
        (
            r"\b(\d{2,7}(?:\.\d+)?)\s*(?:sq\.?\s*yds?|sq\.?\s*yards?|yards?|yds?|gaj)\b",
            "sqyd",
            9.0,
        ),
        (
            r"\b(\d+(?:\.\d+)?)\s*(?:acre|acres)\b",
            "acre",
            43560.0,
        ),
        (
            r"\b(\d{2,8}(?:\.\d+)?)\s*(?:sqmt|sqmts|sq\s*mt|sq\s*mts|sqm|sq\.?\s*m|square\s*metres?|square\s*meters?)\b",
            "sqm",
            10.7639,
        ),
    ]

    for pattern, unit, multiplier in patterns:
        m = re.search(pattern, raw, re.I)
        if m:
            value = float(m.group(1))
            return {
                "value": value,
                "unit": unit,
                "sqft": round(value * multiplier, 2),
                "evidence": m.group(0).strip(),
            }

    return None


def _money_value(number: float, unit: Optional[str]) -> float:
    u = _norm(unit)

    if u in ("CR", "CRORE", "CRORES"):
        return number * 10000000

    if u in ("L", "LAC", "LACS", "LAKH", "LAKHS"):
        return number * 100000

    if u in ("K", "THOUSAND"):
        return number * 1000

    return number


def _commercial_terms(raw: str, transaction: Optional[str]) -> Optional[Dict[str, Any]]:
    sale_patterns = [
        r"(?:owner'?s?\s+wants?|owners?\s+wants?|asking|demand(?:ing)?|expecting|quoted\s+at|price|outright)\s*(?:only\s*)?(?:[:=@-]\s*)?(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)\b",
        r"(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)\s*(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)\b",
    ]

    rent_patterns = [
        r"(?:rent|rental|lease|monthly\s+rent)\s*(?:[:=@-]\s*)?(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)?\b",
        r"(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)\s*(?:/|-)?\s*(?:month|monthly|pm|p\.m\.)\b",
    ]

    patterns = rent_patterns if transaction == "RENT" else sale_patterns

    for pattern in patterns:
        m = re.search(pattern, raw, re.I)
        if m:
            number = float(m.group(1))
            unit = m.group(2) if m.lastindex and m.lastindex >= 2 else None

            return {
                "value": _money_value(number, unit),
                "raw": m.group(0).strip(),
            }

    return None


def _transaction(raw: str, current: Optional[str]) -> tuple[Optional[str], Optional[str], float]:
    if current:
        return current, current, 0.95

    n = _norm(raw)

    for signal in RENT_CONTEXT:
        if signal in n:
            return "RENT", signal, 0.92

    for signal in SALE_CONTEXT:
        if signal in n:
            if re.search(r"\b\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh)\b", raw, re.I):
                return "SALE", signal, 0.92

    if (
        any(x in n for x in PROPERTY_SIGNALS)
        and re.search(r"\b\d+(?:\.\d+)?\s*(?:cr|crore)\b", raw, re.I)
        and not any(x in n for x in RENT_CONTEXT)
    ):
        return "SALE", "property + crore asking context", 0.82

    return None, None, 0.0


def _property_family(raw: str, current: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if current:
        return current, current

    n = _norm(raw)

    if any(x in n for x in (
        "OFFICE", "SHOP", "SHOWROOM", "RETAIL", "COMMERCIAL",
        "RESTAURANT", "BANQUET", "WAREHOUSE", "GODOWN"
    )):
        return "COMMERCIAL", "commercial property signal"

    if any(x in n for x in (
        "BHK", "FLAT", "APARTMENT", "VILLA",
        "KOTHI", "PENTHOUSE", "RESIDENTIAL"
    )):
        return "RESIDENTIAL", "residential property signal"

    if any(x in n for x in (
        "PLOT", "LAND", "ACRE", "FARMHOUSE"
    )):
        return "LAND", "land property signal"

    return None, None


def _location(raw: str, current: Optional[str]) -> tuple[Optional[str], Optional[str], float]:
    if current:
        return current, current, 0.95

    patterns = [
        r"\b(?:plot|land|villa|flat|apartment|office|shop|showroom|property|farmhouse)\s+(?:in|at)\s+([A-Za-z][A-Za-z .'-]{2,40})",
        r"\blocation\s*[:=-]\s*([A-Za-z][A-Za-z .'-]{2,40})",
        r"\b(?:located|situated)\s+(?:in|at)\s+([A-Za-z][A-Za-z .'-]{2,40})",
    ]

    for pattern in patterns:
        m = re.search(pattern, raw, re.I)

        if not m:
            continue

        place = re.split(
            r"[\n\r,;|()]|\s+-\s+|\s+AREA\b|\s+SIZE\b|\s+PRICE\b",
            m.group(1),
            maxsplit=1,
            flags=re.I,
        )[0].strip(" -*📍")

        place = re.sub(r"\s+", " ", place).strip()

        words = place.split()

        if len(words) > 5:
            place = " ".join(words[:5])

        if place:
            return place.title(), m.group(0).strip(), 0.88

    return None, None, 0.0


def _suitable_uses(raw: str):
    n = _norm(raw)
    out = []

    for use, signals in USE_PATTERNS.items():
        if any(s in n for s in signals):
            out.append(use)

    return list(dict.fromkeys(out))


def _features(raw: str):
    n = _norm(raw)
    out = {}

    for feature, signals in FEATURE_PATTERNS.items():
        if any(s in n for s in signals):
            out[feature] = True

    return out


def _negotiability(raw: str):
    n = _norm(raw)

    if any(x in n for x in (
        "NEGOTIABLE",
        "NEGOTIATED",
        "PRICE CAN BE NEGOTIATED",
        "NEGOTIATE",
    )):
        return True

    if any(x in n for x in (
        "NON NEGOTIABLE",
        "NON-NEGOTIABLE",
        "FIXED PRICE",
    )):
        return False

    return None


def _listing_signal(raw: str) -> bool:
    n = _norm(raw)

    property_signal = any(x in n for x in PROPERTY_SIGNALS)

    commercial_signal = bool(
        re.search(
            r"\b(?:₹|rs\.?|inr)?\s*\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh)\b",
            raw,
            re.I,
        )
    )

    return property_signal and commercial_signal


def _needs_llm(fields: Dict[str, Any], classification: str) -> bool:
    core = (
        fields.get("transaction"),
        fields.get("property_family"),
        fields.get("location_raw"),
        fields.get("area"),
        fields.get("money"),
    )

    missing = sum(v in (None, "", [], {}) for v in core)

    return classification == "AMBIGUOUS" or missing >= 2


def _extract_json(text_value: str):
    value = str(text_value or "").strip()

    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)

    start = value.find("{")
    end = value.rfind("}")

    if start >= 0 and end > start:
        value = value[start:end + 1]

    return json.loads(value)


def _gemini_understanding(raw: str) -> Optional[Dict[str, Any]]:
    key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENAI_API_KEY")
    )

    if not key:
        return None

    try:
        from google import genai
    except Exception:
        return None

    model = os.getenv(
        "ALLIANCE_PROPERTY_AI_MODEL",
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    )

    prompt = f"""
You are Alliance Property AI.

Understand ONE real-estate listing or requirement.

Never invent missing facts.

Return ONLY JSON.

Required schema:
{{
  "classification": "AVAILABILITY|REQUIREMENT|AMBIGUOUS|NOISE",
  "transaction": {{
    "value": "SALE|RENT|null",
    "confidence": 0.0,
    "evidence": ""
  }},
  "property_family": {{
    "value": "COMMERCIAL|RESIDENTIAL|LAND|null",
    "confidence": 0.0,
    "evidence": ""
  }},
  "location": {{
    "value": null,
    "confidence": 0.0,
    "evidence": ""
  }},
  "area": {{
    "value": null,
    "unit": null,
    "confidence": 0.0,
    "evidence": ""
  }},
  "price": {{
    "value_inr": null,
    "confidence": 0.0,
    "evidence": ""
  }},
  "configuration": {{
    "value": null,
    "confidence": 0.0,
    "evidence": ""
  }},
  "suitable_uses": [],
  "features": {{}},
  "negotiable": null,
  "notes": []
}}

Rules:
- "Owners wants 5cr", "asking 5cr", "demand 5cr" in a property listing
  are strong SALE-price signals unless rent context is explicit.
- sqmt, sq mt, sq mts, sqm and square metres are square metres.
- "good for single luxury villa" is suitable-use evidence.
- Preserve uncertainty.
- Do not treat phone numbers as prices.
- Do not expose or repeat contact numbers in the JSON.

TEXT:
{raw}
"""

    try:
        client = genai.Client(api_key=key)

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )

        return _extract_json(response.text)

    except Exception:
        return None


def _merge_llm(fields, confidence, ai, raw):
    if not isinstance(ai, dict):
        return fields, confidence

    tx = ai.get("transaction") or {}

    if not fields.get("transaction") and tx.get("value") in ("SALE", "RENT"):
        if float(tx.get("confidence") or 0) >= 0.72:
            fields["transaction"] = tx["value"]
            confidence["transaction"] = float(tx.get("confidence"))

    fam = ai.get("property_family") or {}

    if not fields.get("property_family") and fam.get("value") in (
        "COMMERCIAL",
        "RESIDENTIAL",
        "LAND",
    ):
        if float(fam.get("confidence") or 0) >= 0.72:
            fields["property_family"] = fam["value"]
            confidence["property_family"] = float(fam.get("confidence"))

    loc = ai.get("location") or {}

    if not fields.get("location_raw") and loc.get("value"):
        if float(loc.get("confidence") or 0) >= 0.75:
            fields["location_raw"] = str(loc["value"]).strip()
            confidence["location_raw"] = float(loc.get("confidence"))

    area = ai.get("area") or {}

    if not fields.get("area") and area.get("value") and area.get("unit"):
        if float(area.get("confidence") or 0) >= 0.80:
            unit = str(area["unit"]).lower()
            multiplier = {
                "sqft": 1,
                "sqyd": 9,
                "sqm": 10.7639,
                "acre": 43560,
            }.get(unit)

            if multiplier:
                val = float(area["value"])

                fields["area"] = {
                    "value": val,
                    "unit": unit,
                    "sqft": round(val * multiplier, 2),
                    "evidence": area.get("evidence"),
                }

                confidence["area"] = float(area.get("confidence"))

    price = ai.get("price") or {}

    if not fields.get("money") and price.get("value_inr"):
        if float(price.get("confidence") or 0) >= 0.80:
            fields["money"] = {
                "value": float(price["value_inr"]),
                "raw": price.get("evidence") or "",
            }

            confidence["money"] = float(price.get("confidence"))

    cfg = ai.get("configuration") or {}

    if not fields.get("configuration") and cfg.get("value"):
        if float(cfg.get("confidence") or 0) >= 0.75:
            fields["configuration"] = str(cfg["value"]).strip()
            confidence["configuration"] = float(cfg.get("confidence"))

    if ai.get("suitable_uses"):
        fields["suitable_uses"] = list(
            dict.fromkeys(
                (fields.get("suitable_uses") or [])
                + [
                    str(x).strip()
                    for x in ai.get("suitable_uses") or []
                    if str(x).strip()
                ]
            )
        )

    if isinstance(ai.get("features"), dict):
        merged = dict(fields.get("features") or {})
        merged.update(
            {
                str(k): bool(v)
                for k, v in ai["features"].items()
                if v is True
            }
        )
        fields["features"] = merged

    if fields.get("negotiable") is None and ai.get("negotiable") is not None:
        fields["negotiable"] = bool(ai.get("negotiable"))

    return fields, confidence


def enhance_extraction(base):
    raw = str(base.fields.get("raw_text") or "")

    fields = dict(base.fields)
    confidence = dict(base.field_confidence)

    transaction, tx_evidence, tx_conf = _transaction(
        raw,
        fields.get("transaction"),
    )

    fields["transaction"] = transaction

    if tx_conf:
        confidence["transaction"] = max(
            confidence.get("transaction", 0),
            tx_conf,
        )

    family, family_evidence = _property_family(
        raw,
        fields.get("property_family"),
    )

    fields["property_family"] = family

    if family:
        confidence["property_family"] = max(
            confidence.get("property_family", 0),
            0.92,
        )

    location, location_evidence, location_conf = _location(
        raw,
        fields.get("location_raw"),
    )

    fields["location_raw"] = location

    if location_conf:
        confidence["location_raw"] = max(
            confidence.get("location_raw", 0),
            location_conf,
        )

    if not fields.get("area"):
        area = _area(raw)

        if area:
            fields["area"] = area
            confidence["area"] = 0.97

    if not fields.get("money") and fields.get("transaction"):
        commercial_terms = _commercial_terms(
            raw,
            fields.get("transaction"),
        )

        if commercial_terms:
            fields["money"] = commercial_terms
            confidence["money"] = 0.94

    fields["suitable_uses"] = _suitable_uses(raw)
    fields["features"] = _features(raw)
    fields["negotiable"] = _negotiability(raw)

    evidence = {
        "transaction": tx_evidence,
        "property_family": family_evidence,
        "location": location_evidence,
        "area": (
            fields.get("area", {}).get("evidence")
            if isinstance(fields.get("area"), dict)
            else None
        ),
        "price": (
            fields.get("money", {}).get("raw")
            if isinstance(fields.get("money"), dict)
            else None
        ),
        "suitable_uses": fields.get("suitable_uses") or [],
        "features": fields.get("features") or {},
    }

    fields["source_evidence"] = {
        k: v
        for k, v in evidence.items()
        if v not in (None, "", [], {})
    }

    classification = base.classification

    if classification == "AMBIGUOUS" and _listing_signal(raw):
        classification = "AVAILABILITY"

    llm_used = False
    llm_data = None

    if _needs_llm(fields, classification):
        llm_data = _gemini_understanding(raw)

        if llm_data:
            llm_used = True

            fields, confidence = _merge_llm(
                fields,
                confidence,
                llm_data,
                raw,
            )

            llm_class = llm_data.get("classification")

            if (
                classification == "AMBIGUOUS"
                and llm_class in (
                    "AVAILABILITY",
                    "REQUIREMENT",
                    "NOISE",
                )
            ):
                classification = llm_class

    core_present = sum(
        bool(fields.get(k))
        for k in (
            "transaction",
            "property_family",
            "location_raw",
            "area",
            "money",
        )
    )

    confidence["overall"] = min(
        0.99,
        round(0.35 + core_present * 0.12, 2),
    )

    fields["ai_understanding"] = {
        "engine": VERSION,
        "mode": "HYBRID_LLM" if llm_used else "HYBRID_RULES",
        "source_evidence": fields.get("source_evidence") or {},
        "llm_used": llm_used,
        "llm_model": (
            os.getenv(
                "ALLIANCE_PROPERTY_AI_MODEL",
                os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            )
            if llm_used
            else None
        ),
    }

    return base.model_copy(
        update={
            "classification": classification,
            "fields": fields,
            "field_confidence": confidence,
            "extraction_method": "hybrid",
        }
    )


def _canonical_enrichment(engine, property_id, extraction):
    fields = extraction.fields

    suitable_uses = fields.get("suitable_uses") or []
    source_evidence = fields.get("source_evidence") or {}
    ai_understanding = fields.get("ai_understanding") or {}

    core = (
        fields.get("transaction"),
        fields.get("property_family"),
        fields.get("location_raw"),
        fields.get("area"),
        fields.get("money"),
    )

    complete = sum(bool(v) for v in core)

    data_quality_status = (
        "CLEAN"
        if complete >= 4
        and fields.get("transaction")
        and fields.get("location_raw")
        else "UNDER_REVIEW"
    )

    with engine.begin() as c:
        c.execute(
            text("""
                UPDATE pb_canonical_properties
                SET
                    ai_understanding = CAST(:ai AS jsonb),
                    data_quality_status = :quality,
                    suitable_uses = CAST(:uses AS jsonb),
                    negotiability = :neg,
                    source_evidence = CAST(:evidence AS jsonb),
                    entity_version = COALESCE(entity_version,1) + 1,
                    updated_at = NOW()
                WHERE property_id = :pid
            """),
            {
                "pid": property_id,
                "ai": json.dumps(ai_understanding, default=str),
                "quality": data_quality_status,
                "uses": json.dumps(suitable_uses, default=str),
                "neg": fields.get("negotiable"),
                "evidence": json.dumps(source_evidence, default=str),
            },
        )


def _parra_demo():
    from uuid import uuid4
    from property_brain.schemas import Segment
    from property_brain.stages.s4_extractor import extract as original_extract

    text_value = """
Plot in PARRA (Prime Location)
Area: 721 sqmt
Good Shape
Tar Road
Compounded
Good For Single Luxury Villa
Owners Wants only: 5cr
Price can be negotiated more if quick payment. Has MOU.
"""

    seg = Segment(
        segment_id=uuid4(),
        raw_ids=[],
        text=text_value,
        split_method="single",
        burst_group_id=uuid4(),
        insufficient=False,
    )

    base = original_extract(seg)
    enhanced = enhance_extraction(base)

    return {
        "classification": enhanced.classification,
        "transaction": enhanced.fields.get("transaction"),
        "property_family": enhanced.fields.get("property_family"),
        "location": enhanced.fields.get("location_raw"),
        "area": enhanced.fields.get("area"),
        "money": enhanced.fields.get("money"),
        "suitable_uses": enhanced.fields.get("suitable_uses"),
        "features": enhanced.fields.get("features"),
        "negotiable": enhanced.fields.get("negotiable"),
        "source_evidence": enhanced.fields.get("source_evidence"),
        "ai_understanding": enhanced.fields.get("ai_understanding"),
        "field_confidence": enhanced.field_confidence,
    }


def register(core):
    app = core.app
    engine = core.engine

    if getattr(app.state, "alliance_property_ai_v1_registered", False):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": "/api/v7/property-ai/status",
        }

    import property_brain.orchestrator as orchestrator
    import property_brain.stages.s4_extractor as s4
    import property_brain.stages.s8_entity_resolution as s8

    original_extract = s4.extract
    original_resolve = s8.resolve_and_upsert

    if not getattr(orchestrator, "_alliance_v7_original_extract", None):
        orchestrator._alliance_v7_original_extract = orchestrator.extract

    if not getattr(orchestrator, "_alliance_v7_original_resolve", None):
        orchestrator._alliance_v7_original_resolve = (
            orchestrator.resolve_and_upsert
        )

    def hybrid_extract(seg):
        base = original_extract(seg)
        return enhance_extraction(base)

    def hybrid_resolve(engine_arg, extraction, loc, source_meta):
        pid = original_resolve(
            engine_arg,
            extraction,
            loc,
            source_meta,
        )

        try:
            _canonical_enrichment(
                engine_arg,
                pid,
                extraction,
            )
        except Exception:
            pass

        return pid

    orchestrator.extract = hybrid_extract
    orchestrator.resolve_and_upsert = hybrid_resolve

    @app.get("/api/v7/property-ai/status")
    def property_ai_status():
        key_present = bool(
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("GOOGLE_GENAI_API_KEY")
        )

        return JSONResponse(
            {
                "status": "READY",
                "version": VERSION,
                "mode": (
                    "HYBRID_RULES_PLUS_GEMINI"
                    if key_present
                    else "HYBRID_RULES"
                ),
                "gemini_key_present": key_present,
                "model": os.getenv(
                    "ALLIANCE_PROPERTY_AI_MODEL",
                    os.getenv(
                        "GEMINI_MODEL",
                        "gemini-2.5-flash",
                    ),
                ),
                "orchestrator_patched": (
                    orchestrator.extract is hybrid_extract
                ),
                "raw_data_deleted": False,
                "matcher_modified": False,
                "whatsapp_live_modified": False,
            }
        )

    @app.get("/api/v7/property-ai/parra-test")
    def property_ai_parra_test():
        return JSONResponse(_parra_demo())

    @app.get("/api/v7/property-ai/review-preview")
    def review_preview(
        limit: int = Query(100, ge=1, le=250)
    ):
        rows = []

        with engine.connect() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    text("""
                        SELECT
                            rq.review_id::text AS review_id,
                            rq.reason,
                            rq.target_id::text AS extraction_id,
                            e.classification,
                            e.fields,
                            e.field_confidence
                        FROM pb_review_queue rq
                        JOIN pb_extractions e
                          ON e.extraction_id = rq.target_id
                        WHERE rq.status='OPEN'
                          AND rq.queue_type='holding'
                        ORDER BY rq.created_at DESC
                        LIMIT :lim
                    """),
                    {"lim": limit},
                ).mappings().all()
            ]

        recovered = []
        unchanged = []

        for row in rows:
            fields = row.get("fields") or {}

            raw = fields.get("raw_text") or ""

            fake = type("ExtractionProxy", (), {})()
            fake.classification = row.get("classification") or "AMBIGUOUS"
            fake.fields = fields
            fake.field_confidence = row.get("field_confidence") or {}

            def _copy(update=None):
                obj = type("EnhancedProxy", (), {})()

                for attr in (
                    "classification",
                    "fields",
                    "field_confidence",
                    "extraction_method",
                ):
                    setattr(
                        obj,
                        attr,
                        getattr(fake, attr, None),
                    )

                for key, value in (update or {}).items():
                    setattr(obj, key, value)

                return obj

            fake.model_copy = _copy

            enhanced = enhance_extraction(fake)

            before_core = sum(
                bool(fields.get(k))
                for k in (
                    "transaction",
                    "property_family",
                    "location_raw",
                    "area",
                    "money",
                )
            )

            after_core = sum(
                bool(enhanced.fields.get(k))
                for k in (
                    "transaction",
                    "property_family",
                    "location_raw",
                    "area",
                    "money",
                )
            )

            item = {
                "review_id": row["review_id"],
                "reason": row.get("reason"),
                "before_core_fields": before_core,
                "after_core_fields": after_core,
                "classification_before": row.get("classification"),
                "classification_after": enhanced.classification,
                "location": enhanced.fields.get("location_raw"),
                "transaction": enhanced.fields.get("transaction"),
                "property_family": enhanced.fields.get("property_family"),
                "area": enhanced.fields.get("area"),
                "money": enhanced.fields.get("money"),
                "ai_mode": (
                    enhanced.fields
                    .get("ai_understanding", {})
                    .get("mode")
                ),
            }

            if after_core > before_core:
                recovered.append(item)
            else:
                unchanged.append(item)

        return JSONResponse(
            {
                "version": VERSION,
                "sample_size": len(rows),
                "improved_records": len(recovered),
                "unchanged_records": len(unchanged),
                "recovery_improvement_rate": (
                    round(
                        len(recovered) / len(rows) * 100,
                        2,
                    )
                    if rows
                    else 0
                ),
                "important": (
                    "Preview only. Existing review records "
                    "have not been modified."
                ),
                "improved": recovered,
                "unchanged": unchanged,
            }
        )

    app.state.alliance_property_ai_v1_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": "/api/v7/property-ai/status",
        "parra_test": "/api/v7/property-ai/parra-test",
        "review_preview": (
            "/api/v7/property-ai/review-preview?limit=100"
        ),
        "non_destructive": True,
    }
