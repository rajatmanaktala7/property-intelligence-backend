from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

VERSION = "2.2.0-CONTEXT-AWARE-RESCUE-PREVIEW"

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"
)

EMAIL_RE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.I,
)

PROPERTY_SIGNAL_RE = re.compile(
    r"\b(PLOT|LAND|VILLA|APARTMENT|FLAT|OFFICE|SHOP|SHOWROOM|"
    r"RETAIL|WAREHOUSE|GODOWN|FARMHOUSE|RESTAURANT|BANQUET|"
    r"COMMERCIAL|BHK|PENTHOUSE|KOTHI)\b",
    re.I,
)

CONFIG_RE = re.compile(
    r"\b\d+\s*BHK\b",
    re.I,
)

AREA_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*"
    r"(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|GAJ|"
    r"ACRES?|SQMT|SQM|SQ\s*MTS?)\b",
    re.I,
)

MONEY_RE = re.compile(
    r"(?:₹|RS\.?|INR)?\s*"
    r"\d+(?:\.\d+)?\s*"
    r"(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)\b",
    re.I,
)


def _redact_for_ai(value: Any) -> str:
    s = str(value or "")
    s = PHONE_RE.sub("[PHONE_REDACTED]", s)
    s = EMAIL_RE.sub("[EMAIL_REDACTED]", s)
    return s


def _money_is_plausible(
    money: Any,
    transaction: Optional[str],
) -> bool:

    if not isinstance(money, dict):
        return True

    try:
        value = float(money.get("value"))
    except (TypeError, ValueError):
        return False

    raw = str(money.get("raw") or "")

    if value <= 0:
        return False

    digits = re.sub(r"\D", "", raw)

    if (
        len(digits) in (10, 12)
        and not re.search(
            r"(?:₹|RS|INR|CR|CRORE|LAC|LAKH|"
            r"RENT|PRICE|ASKING|DEMAND)",
            raw,
            re.I,
        )
    ):
        return False

    has_scale = bool(
        re.search(
            r"\b(?:CR|CRORE|CRORES|L|LAC|LACS|"
            r"LAKH|LAKHS|K|THOUSAND)\b",
            raw,
            re.I,
        )
    )

    if not has_scale and value < 1000:
        return False

    tx = str(transaction or "").upper()

    if tx == "SALE" and value < 100000:
        return False

    if tx == "RENT" and value < 1000:
        return False

    return True


def _own_identity(
    target_text: str,
    fields: Dict[str, Any],
) -> Dict[str, bool]:

    return {
        "property_signal": bool(
            PROPERTY_SIGNAL_RE.search(target_text)
        ),
        "area": (
            bool(fields.get("area"))
            or bool(AREA_RE.search(target_text))
        ),
        "money": (
            bool(fields.get("money"))
            or bool(MONEY_RE.search(target_text))
        ),
        "configuration": (
            bool(fields.get("configuration"))
            or bool(CONFIG_RE.search(target_text))
        ),
        "location": bool(fields.get("location_raw")),
    }


def _can_attempt_context(
    identity: Dict[str, bool],
) -> bool:

    # IMPORTANT:
    # A price-only fragment is intentionally NOT sufficient.
    # It could belong to a different sibling property.
    return bool(
        identity.get("property_signal")
        or identity.get("area")
        or identity.get("configuration")
        or identity.get("location")
    )


def _extract_json(
    value: Any,
) -> Optional[Dict[str, Any]]:

    s = str(value or "").strip()

    s = re.sub(
        r"^```(?:json)?\s*",
        "",
        s,
        flags=re.I,
    )

    s = re.sub(
        r"\s*```$",
        "",
        s,
    )

    start = s.find("{")
    end = s.rfind("}")

    if start >= 0 and end > start:
        s = s[start:end + 1]

    try:
        data = json.loads(s)

        if isinstance(data, dict):
            return data

    except Exception:
        pass

    return None


def _context_llm(
    target_text: str,
    parent_burst: str,
    siblings: List[str],
) -> Optional[Dict[str, Any]]:

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
        os.getenv(
            "GEMINI_MODEL",
            "gemini-2.5-flash",
        ),
    )

    target_safe = _redact_for_ai(target_text)
    burst_safe = _redact_for_ai(parent_burst)

    sibling_safe = [
        _redact_for_ai(x)
        for x in siblings[:12]
    ]

    prompt = f"""
You are Alliance Context Rescue AI.

The TARGET SEGMENT may be an incomplete fragment extracted
from a larger WhatsApp BURST.

The parent burst can contain MULTIPLE independent properties.

Your job is conservative context recovery.
Do not guess.

Return ONLY JSON:

{{
  "classification": "AVAILABILITY|REQUIREMENT|AMBIGUOUS|NOISE",
  "ambiguous_sibling_assignment": true,
  "shared_context": {{
    "transaction": {{
      "value": "SALE|RENT|null",
      "confidence": 0.0,
      "evidence": "",
      "inherit_safe": false
    }},
    "property_family": {{
      "value": "COMMERCIAL|RESIDENTIAL|LAND|null",
      "confidence": 0.0,
      "evidence": "",
      "inherit_safe": false
    }},
    "location": {{
      "value": null,
      "confidence": 0.0,
      "evidence": "",
      "inherit_safe": false
    }}
  }},
  "notes": []
}}

STRICT RULES:

1. Never copy price from another sibling.

2. Never copy area from another sibling.

3. Never copy configuration from another sibling.

4. Never copy floor from another sibling.

5. Never copy project-specific facts from another sibling.

6. Only these fields may be inherited:
   transaction
   property_family
   location

7. inherit_safe=true only when the field clearly
   applies to the TARGET SEGMENT.

8. If the parent burst contains multiple properties and
   target-to-property mapping is uncertain:
   ambiguous_sibling_assignment=true.

9. A price-only target is ambiguous unless it independently
   identifies the property it belongs to.

10. Never invent missing facts.

11. Contacts have already been redacted.
    Do not reconstruct contacts.

TARGET SEGMENT:

{target_safe}

PARENT BURST:

{burst_safe}

OTHER SEGMENTS:

{json.dumps(sibling_safe, ensure_ascii=False)}
"""

    try:

        client = genai.Client(
            api_key=key
        )

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )

        return _extract_json(
            response.text
        )

    except Exception:
        return None


def _merge_context(
    fields: Dict[str, Any],
    confidence: Dict[str, Any],
    context: Optional[Dict[str, Any]],
):

    inherited: Dict[str, Any] = {}

    if not isinstance(context, dict):
        return fields, confidence, inherited

    if context.get(
        "ambiguous_sibling_assignment"
    ) is True:
        return fields, confidence, inherited

    shared = (
        context.get("shared_context")
        or {}
    )

    mapping = {
        "transaction": "transaction",
        "property_family": "property_family",
        "location": "location_raw",
    }

    for source_key, field_key in mapping.items():

        item = (
            shared.get(source_key)
            or {}
        )

        if fields.get(field_key):
            continue

        if item.get("inherit_safe") is not True:
            continue

        try:
            conf = float(
                item.get("confidence")
                or 0
            )
        except (TypeError, ValueError):
            conf = 0.0

        if conf < 0.90:
            continue

        value = item.get("value")

        if not value:
            continue

        if (
            field_key == "transaction"
            and value not in ("SALE", "RENT")
        ):
            continue

        if (
            field_key == "property_family"
            and value not in (
                "COMMERCIAL",
                "RESIDENTIAL",
                "LAND",
            )
        ):
            continue

        fields[field_key] = (
            str(value).strip()
        )

        confidence[field_key] = conf

        inherited[field_key] = {
            "source": "PARENT_BURST_CONTEXT",
            "confidence": conf,
            "evidence": str(
                item.get("evidence")
                or ""
            )[:300],
        }

    return (
        fields,
        confidence,
        inherited,
    )


def _quality(
    classification: str,
    fields: Dict[str, Any],
    identity: Dict[str, bool],
    money_rejected: bool,
):

    reasons: List[str] = []

    if classification == "NOISE":
        return (
            "NOISE",
            ["classified_noise"],
        )

    if classification != "AVAILABILITY":
        reasons.append(
            "not_confirmed_availability"
        )

    if not fields.get("transaction"):
        reasons.append(
            "transaction_missing"
        )

    if not fields.get("property_family"):
        reasons.append(
            "property_family_missing"
        )

    if not fields.get("location_raw"):
        reasons.append(
            "location_missing"
        )

    if money_rejected:
        reasons.append(
            "ambiguous_money_rejected"
        )

    specific_own_fact = bool(
        identity.get("area")
        or identity.get("money")
        or identity.get("configuration")
        or identity.get("property_signal")
    )

    if not specific_own_fact:
        reasons.append(
            "no_own_property_identity"
        )

    if reasons:
        return (
            "UNDER_REVIEW",
            reasons,
        )

    return (
        "CLEAN",
        [],
    )


def _install_runtime_guards():

    import alliance_property_ai_v1 as pai

    if not getattr(
        pai,
        "_v22_privacy_guard_installed",
        False,
    ):

        original_gemini = (
            pai._gemini_understanding
        )

        def safe_gemini(raw: str):

            safe_raw = _redact_for_ai(
                raw
            )

            return original_gemini(
                safe_raw
            )

        pai._gemini_understanding = (
            safe_gemini
        )

        pai._v22_privacy_guard_installed = True

    if not getattr(
        pai,
        "_v22_money_guard_installed",
        False,
    ):

        original_enhance = (
            pai.enhance_extraction
        )

        def safe_enhance(base):

            enhanced = (
                original_enhance(base)
            )

            fields = dict(
                enhanced.fields
            )

            confidence = dict(
                enhanced.field_confidence
            )

            money = fields.get("money")

            rejected = False

            if (
                money
                and not _money_is_plausible(
                    money,
                    fields.get(
                        "transaction"
                    ),
                )
            ):

                rejected = True

                fields["money"] = None

                confidence["money"] = 0.0

                ai = dict(
                    fields.get(
                        "ai_understanding"
                    )
                    or {}
                )

                ai["money_guard"] = (
                    "REJECTED_AMBIGUOUS_SCALE"
                )

                fields[
                    "ai_understanding"
                ] = ai

            if rejected:

                source = dict(
                    fields.get(
                        "source_evidence"
                    )
                    or {}
                )

                source.pop(
                    "price",
                    None,
                )

                fields[
                    "source_evidence"
                ] = source

            return enhanced.model_copy(
                update={
                    "fields": fields,
                    "field_confidence": confidence,
                }
            )

        pai.enhance_extraction = (
            safe_enhance
        )

        pai._v22_money_guard_installed = True

    return {
        "privacy_guard": True,
        "money_guard": True,
    }


def _load_rows(
    engine,
    limit: int,
) -> List[Dict[str, Any]]:

    with engine.connect() as c:

        result = c.execute(
            text(
                """
                SELECT
                    rq.review_id::text
                        AS review_id,

                    rq.reason,

                    e.extraction_id::text
                        AS extraction_id,

                    e.classification,

                    e.fields,

                    e.field_confidence,

                    e.extraction_method,

                    s.segment_id::text
                        AS segment_id,

                    s.segment_text,

                    s.split_method,

                    s.burst_group_id::text
                        AS burst_group_id,

                    b.burst_text,

                    COALESCE(
                        (
                            SELECT
                                jsonb_agg(
                                    s2.segment_text
                                    ORDER BY
                                    s2.created_at
                                )
                            FROM pb_segments s2
                            WHERE
                                s2.burst_group_id
                                =
                                s.burst_group_id
                            AND
                                s2.segment_id
                                <>
                                s.segment_id
                        ),
                        '[]'::jsonb
                    )
                    AS sibling_segments

                FROM pb_review_queue rq

                JOIN pb_extractions e
                  ON e.extraction_id
                   = rq.target_id

                JOIN pb_segments s
                  ON s.segment_id
                   = e.segment_id

                JOIN pb_bursts b
                  ON b.burst_group_id
                   = s.burst_group_id

                WHERE
                    rq.status='OPEN'
                AND
                    rq.queue_type='holding'

                ORDER BY
                    rq.created_at DESC

                LIMIT :lim
                """
            ),
            {
                "lim": limit
            },
        ).mappings().all()

        return [
            dict(r)
            for r in result
        ]


def _preview_one(
    row: Dict[str, Any],
    pai,
    use_llm: bool,
):

    target_text = str(
        row.get("segment_text")
        or (
            row.get("fields")
            or {}
        ).get("raw_text")
        or ""
    )

    parent_burst = str(
        row.get("burst_text")
        or ""
    )

    siblings = list(
        row.get("sibling_segments")
        or []
    )

    fields = dict(
        row.get("fields")
        or {}
    )

    confidence = dict(
        row.get("field_confidence")
        or {}
    )

    classification = (
        row.get("classification")
        or "AMBIGUOUS"
    )

    #
    # Deterministic target-only enrichment.
    # No extra Gemini call here.
    #

    transaction, _, tx_conf = (
        pai._transaction(
            target_text,
            fields.get(
                "transaction"
            ),
        )
    )

    fields["transaction"] = (
        transaction
    )

    if tx_conf:

        confidence[
            "transaction"
        ] = max(
            float(
                confidence.get(
                    "transaction"
                )
                or 0
            ),
            tx_conf,
        )

    family, _ = (
        pai._property_family(
            target_text,
            fields.get(
                "property_family"
            ),
        )
    )

    fields[
        "property_family"
    ] = family

    if family:

        confidence[
            "property_family"
        ] = max(
            float(
                confidence.get(
                    "property_family"
                )
                or 0
            ),
            0.92,
        )

    location, _, loc_conf = (
        pai._location(
            target_text,
            fields.get(
                "location_raw"
            ),
        )
    )

    fields[
        "location_raw"
    ] = location

    if loc_conf:

        confidence[
            "location_raw"
        ] = max(
            float(
                confidence.get(
                    "location_raw"
                )
                or 0
            ),
            loc_conf,
        )

    if not fields.get("area"):

        area = pai._area(
            target_text
        )

        if area:

            fields["area"] = area

            confidence[
                "area"
            ] = 0.97

    if (
        not fields.get("money")
        and fields.get(
            "transaction"
        )
    ):

        money = (
            pai._commercial_terms(
                target_text,
                fields.get(
                    "transaction"
                ),
            )
        )

        if money:

            fields["money"] = money

            confidence[
                "money"
            ] = 0.94

    if (
        classification
        == "AMBIGUOUS"
        and pai._listing_signal(
            target_text
        )
    ):

        classification = (
            "AVAILABILITY"
        )

    money_rejected = False

    if (
        fields.get("money")
        and not _money_is_plausible(
            fields.get("money"),
            fields.get(
                "transaction"
            ),
        )
    ):

        fields["money"] = None

        confidence["money"] = 0.0

        money_rejected = True

    identity = _own_identity(
        target_text,
        fields,
    )

    context = None

    inherited: Dict[str, Any] = {}

    if (
        use_llm
        and _can_attempt_context(
            identity
        )
    ):

        context = _context_llm(
            target_text=target_text,
            parent_burst=parent_burst,
            siblings=siblings,
        )

        (
            fields,
            confidence,
            inherited,
        ) = _merge_context(
            fields,
            confidence,
            context,
        )

    if (
        context
        and classification
        == "AMBIGUOUS"
        and context.get(
            "classification"
        )
        in (
            "AVAILABILITY",
            "REQUIREMENT",
            "NOISE",
        )
    ):

        classification = context[
            "classification"
        ]

    (
        quality,
        reasons,
    ) = _quality(
        classification,
        fields,
        identity,
        money_rejected,
    )

    return {

        "review_id":
            row.get("review_id"),

        "reason_before":
            row.get("reason"),

        "segment_id":
            row.get("segment_id"),

        "burst_group_id":
            row.get(
                "burst_group_id"
            ),

        "split_method":
            row.get(
                "split_method"
            ),

        "classification_before":
            row.get(
                "classification"
            ),

        "classification_after":
            classification,

        "quality_after":
            quality,

        "quality_reasons":
            reasons,

        "location":
            fields.get(
                "location_raw"
            ),

        "transaction":
            fields.get(
                "transaction"
            ),

        "property_family":
            fields.get(
                "property_family"
            ),

        "area":
            fields.get("area"),

        "money":
            fields.get("money"),

        "own_identity":
            identity,

        "inherited_context":
            inherited,

        "context_ai_used":
            context is not None,

        "context_ambiguous":
            (
                context.get(
                    "ambiguous_sibling_assignment"
                )
                if isinstance(
                    context,
                    dict,
                )
                else None
            ),

        "sibling_count":
            len(siblings),

        "privacy_redaction":
            True,

        "money_guard_rejected":
            money_rejected,
    }


def register(core):

    app = core.app
    engine = core.engine

    if getattr(
        app.state,
        "alliance_property_context_rescue_v22_registered",
        False,
    ):

        return {
            "status":
                "ALREADY_REGISTERED",

            "version":
                VERSION,

            "route":
                "/api/v7/property-ai/context-rescue/status",
        }

    guards = (
        _install_runtime_guards()
    )

    @app.get(
        "/api/v7/property-ai/context-rescue/status"
    )
    def status():

        key_present = bool(
            os.getenv(
                "GEMINI_API_KEY"
            )
            or os.getenv(
                "GOOGLE_API_KEY"
            )
            or os.getenv(
                "GOOGLE_GENAI_API_KEY"
            )
        )

        return JSONResponse(
            {
                "status":
                    "READY",

                "version":
                    VERSION,

                "mode":
                    "READ_ONLY_BENCHMARK",

                "parent_burst_context":
                    True,

                "sibling_isolation":
                    True,

                "allowed_inherited_fields":
                    [
                        "transaction",
                        "property_family",
                        "location",
                    ],

                "forbidden_inherited_fields":
                    [
                        "price",
                        "area",
                        "configuration",
                        "floor",
                        "project",
                    ],

                "privacy_redaction":
                    guards[
                        "privacy_guard"
                    ],

                "money_scale_guard":
                    guards[
                        "money_guard"
                    ],

                "gemini_key_present":
                    key_present,

                "writes_enabled":
                    False,

                "backfill_enabled":
                    False,

                "matcher_modified":
                    False,

                "whatsapp_live_modified":
                    False,

                "raw_data_deleted":
                    False,
            }
        )

    @app.get(
        "/api/v7/property-ai/context-rescue/preview"
    )
    def preview(
        limit: int = Query(
            100,
            ge=1,
            le=250,
        ),
        ai_limit: int = Query(
            25,
            ge=0,
            le=100,
        ),
    ):

        import alliance_property_ai_v1 as pai

        rows = _load_rows(
            engine,
            limit,
        )

        items = []

        ai_attempts = 0

        for row in rows:

            fields = (
                row.get("fields")
                or {}
            )

            target_text = str(
                row.get(
                    "segment_text"
                )
                or fields.get(
                    "raw_text"
                )
                or ""
            )

            identity = (
                _own_identity(
                    target_text,
                    fields,
                )
            )

            eligible = (
                _can_attempt_context(
                    identity
                )
            )

            use_llm = (
                eligible
                and ai_attempts
                < ai_limit
            )

            if use_llm:
                ai_attempts += 1

            items.append(
                _preview_one(
                    row,
                    pai,
                    use_llm,
                )
            )

        clean = [
            x
            for x in items
            if x["quality_after"]
            == "CLEAN"
        ]

        under_review = [
            x
            for x in items
            if x["quality_after"]
            == "UNDER_REVIEW"
        ]

        noise = [
            x
            for x in items
            if x["quality_after"]
            == "NOISE"
        ]

        inherited = [
            x
            for x in items
            if x[
                "inherited_context"
            ]
        ]

        ambiguous_money = [
            x
            for x in items
            if x[
                "money_guard_rejected"
            ]
        ]

        ai_success = [
            x
            for x in items
            if x[
                "context_ai_used"
            ]
        ]

        return JSONResponse(
            {
                "version":
                    VERSION,

                "sample_size":
                    len(items),

                "ai_limit":
                    ai_limit,

                "ai_attempts":
                    ai_attempts,

                "ai_successes":
                    len(ai_success),

                "clean_candidates":
                    len(clean),

                "under_review":
                    len(under_review),

                "noise":
                    len(noise),

                "context_inheritance_used":
                    len(inherited),

                "ambiguous_money_rejected":
                    len(
                        ambiguous_money
                    ),

                "safe_recovery_rate":
                    (
                        round(
                            len(clean)
                            / len(items)
                            * 100,
                            2,
                        )
                        if items
                        else 0
                    ),

                "important":
                    (
                        "READ ONLY. No review row, extraction, "
                        "canonical property, raw evidence, matcher "
                        "or WhatsApp Live record was modified."
                    ),

                "clean":
                    clean,

                "under_review_examples":
                    under_review[:30],

                "noise_examples":
                    noise[:20],
            }
        )

    app.state.alliance_property_context_rescue_v22_registered = True

    return {
        "status":
            "REGISTERED",

        "version":
            VERSION,

        "route":
            "/api/v7/property-ai/context-rescue/status",

        "preview":
            (
                "/api/v7/property-ai/context-rescue/preview"
                "?limit=100&ai_limit=25"
            ),

        "non_destructive":
            True,

        "writes_enabled":
            False,
    }