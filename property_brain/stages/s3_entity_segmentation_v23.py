from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

VERSION = "2.3.3-MIXED-TRANSACTION-BUNDLE-SPLITTER"

# ----------------------------
# Core entity signals
# ----------------------------

NUMBERED_RE = re.compile(
    r"(?m)^\s*(?:\d{1,2}[.)](?=\s|[A-Za-z*])|[1-9]️⃣|[①②③④⑤⑥⑦⑧⑨])\s*"
)
INLINE_NUMBERED_RE = re.compile(
    r"\s+(?=(?:\d{1,2})[.)](?=\s|[A-Za-z*]))"
)

UNIT_ANCHOR_RE = re.compile(
    r"(?i)(?<![A-Z0-9])(?:B|UNIT|SHOP|OFFICE|PLOT)\s*[-:#]?\s*\d{2,5}\b"
)
BLOCK_ANCHOR_RE = re.compile(r"(?i)\b[A-Z]\s+BLOCK\b")
LOCALITY_ANCHOR_RE = re.compile(
    r"(?i)\b(?:DLF\s*PHASE\s*[1-5]|SUSHANT\s*LOK\s*1|SHUSHANT\s*LOK\s*1)\b"
)
BUILDING_RE = re.compile(
    r"(?i)\b(?:BUILDING|PROJECT|PROPERTY|OPTION)\s*[-:#]\s*"
)

# Supports "4 BHK", "4BR", "4.br", "3 br.", "4 bedrooms".
CONFIG_RE = re.compile(
    r"(?i)\b(?:"
    r"\d(?:\.\d)?\s*(?:BHK|BEDROOMS?)|"
    r"[2-9](?:/[2-9])+\s*BHK|"
    r"\d\s*\.?\s*BR\.?"
    r")\b"
)

PROPERTY_RE = re.compile(
    r"(?i)\b(?:APARTMENT|FLAT|BUILDER\s+FLOOR|FLOOR|VILLA|KOTHI|BUNGALOW|ROW\s+HOUSE|"
    r"PLOT|LAND|OFFICE|SHOWROOM|SHOP|WAREHOUSE|GODOWN|FARMHOUSE|BANQUET|HOTEL|"
    r"GUEST\s*HOUSE|RESTAURANT|CAFE|CLUB|LOUNGE|COMMERCIAL\s+SPACE|COMMERCIAL\s+BUILDING|"
    r"BANK|BASEMENT|PENTHOUSE)\b"
)

AREA_RE = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|SYDS?|YARDS?|GAJ|GJJ|"
    r"ACRES?|ACER|BIGHA|MTR|MTRS|METER|METERS|SQMT|SQM|SQ\s*MTS?|"
    r"SQUARE\s+METRES?|SQUARE\s+METERS?)\b"
)

MONEY_RE = re.compile(
    r"(?i)(?:(?:₹|RS\.?|INR)\s*)?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)\b"
)

LABELED_MONEY_RE = re.compile(
    r"(?i)\b(?:RENT|PRICE|ASKING|DEMAND|SALE\s+PRICE|RATE)\s*[:=@-]?\s*"
    r"(?:₹|RS\.?|INR)?\s*\d"
)

LOCATION_LABEL_RE = re.compile(r"(?i)\bLOCATION\s*[:\-]")
TENANT_RE = re.compile(r"(?i)\bTENANT\s*[:\-]")

# ----------------------------
# Intent / section signals
# ----------------------------

EXPLICIT_SALE_RE = re.compile(
    r"(?i)\b(?:FOR\s+SALE|AVAILABLE\s+FOR\s+SALE|AVL\s+FOR\s+SALE|"
    r"ON\s+SALE|SALE\s+OPTION|RESALE|OUTRIGHT)\b"
)

EXPLICIT_RENT_RE = re.compile(
    r"(?i)\b(?:FOR\s+RENT|AVAILABLE\s+FOR\s+RENT|AVL\s+FOR\s+RENT|"
    r"AVAILABLE\s+ON\s+RENT|FOR\s+LEASE|TO\s+LET)\b"
)

TRANSACTION_SECTION_RE = re.compile(
    r"(?i)\b(?:"
    r"AVAILABLE\s+FOR\s+SALE|AVL\s+FOR\s+SALE|FOR\s+SALE|"
    r"AVAILABLE\s+FOR\s+RENT|AVL\s+FOR\s+RENT|FOR\s+RENT|"
    r"AVAILABLE\s+ON\s+RENT|FOR\s+LEASE|TO\s+LET"
    r")\b"
)

STRONG_REQUIREMENT_RE = re.compile(
    r"(?i)(?:"
    r"^\s*(?:DIRECT\s+CLIENT\s+)?(?:RENTAL\s+|PURCHASE\s+|SALE\s+)?REQUIREMENT\b|"
    r"\bURGENT(?:LY)?\s+REQUIRED\b|"
    r"\bCLIENT\s+(?:IS\s+)?LOOKING\s+FOR\b|"
    r"\bWE\s+(?:ARE\s+)?LOOKING\s+FOR\b|"
    r"\bREQUIRED\s*[:\-]"
    r")"
)

FORBIDDEN_CONTEXT_RE = re.compile(
    r"(?i)(?:"
    r"\b[A-Z]\s+BLOCK\b|"
    r"\bSECTOR\s*\d+\b|"
    r"\bPHASE\s*\d+\b|"
    r"\b(?:BUILDING|PROJECT|PROPERTY|OPTION|UNIT|SHOP|OFFICE|PLOT)\s*[-:#]?\s*[A-Z0-9-]+\b|"
    r"\bFLOOR\b|"
    r"\bFACING\b|"
    r"\bTENANT\b|"
    r"\b\d(?:\.\d)?\s*BHK\b"
    r")"
)

TAIL_ASSET_RE = re.compile(
    r"(?i)(?=\b(?:"
    r"AVAILABLE\s+NEWLY\s+CONSTRUCT(?:ED|RD)|"
    r"COMMERCIAL\s+LAND\s+AVAILABLE\s+FOR\s+SALE|"
    r"LAND\s+AVAILABLE\s+FOR\s+SALE|"
    r"PLOT\s+FOR\s+SALE|"
    r"APARTMENT\s+AVAILABLE\s+FOR\s+SALE|"
    r"FLAT\s+FOR\s+SALE"
    r")\b)"
)


@dataclass
class EntityBlock:
    index: int
    own_text: str
    inherited_context: List[str] = field(default_factory=list)
    sibling_facts_do_not_copy: List[str] = field(default_factory=list)

    # Parent/group material may help later AI reasoning, but Phase 2.3.3 explicitly
    # marks it REFERENCE ONLY. It is not auto-applied to canonical fields.
    parent_context_reference_only: List[str] = field(default_factory=list)

    method: str = "single"
    needs_split: bool = False
    reason: str | None = None


def norm(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\u00a0", " "),
    ).strip()


def _fact_count(value: str) -> int:
    s = str(value or "")
    return (
        int(bool(AREA_RE.search(s)))
        + int(bool(MONEY_RE.search(s) or LABELED_MONEY_RE.search(s)))
        + int(bool(CONFIG_RE.search(s)))
    )


def _looks_like_entity(piece: str) -> bool:
    s = norm(piece)

    object_signal = bool(
        PROPERTY_RE.search(s)
        or CONFIG_RE.search(s)
        or UNIT_ANCHOR_RE.search(s)
        or BLOCK_ANCHOR_RE.search(s)
    )

    return object_signal and _fact_count(s) >= 1


def _numbered_record_like(piece: str) -> bool:
    s = norm(piece)

    identity = bool(
        LOCATION_LABEL_RE.search(s)
        or TENANT_RE.search(s)
        or UNIT_ANCHOR_RE.search(s)
    )

    facts = (
        int(bool(AREA_RE.search(s)))
        + int(bool(MONEY_RE.search(s) or LABELED_MONEY_RE.search(s)))
    )

    return identity and facts >= 2


def _has_property_specific_facts(value: str) -> bool:
    s = norm(value)

    return bool(
        CONFIG_RE.search(s)
        or AREA_RE.search(s)
        or MONEY_RE.search(s)
        or LABELED_MONEY_RE.search(s)
        or FORBIDDEN_CONTEXT_RE.search(s)
    )


def _strict_header_context(prefix: str) -> List[str]:
    """
    Context firewall from 2.3.2 remains intact.
    Only proven GLOBAL intent headers can be inherited.
    """
    p = norm(prefix)

    if not p or _has_property_specific_facts(p):
        return []

    if PROPERTY_RE.search(p) and len(p.split()) > 6:
        return []

    if STRONG_REQUIREMENT_RE.search(p):
        return ["REQUIREMENT"]

    sale = bool(EXPLICIT_SALE_RE.search(p))
    rent = bool(EXPLICIT_RENT_RE.search(p))

    if sale and not rent:
        return ["SALE"]

    if rent and not sale:
        return ["RENT"]

    return []


def _transaction_label(value: str) -> str | None:
    sale = bool(EXPLICIT_SALE_RE.search(value))
    rent = bool(EXPLICIT_RENT_RE.search(value))

    if sale and not rent:
        return "SALE"

    if rent and not sale:
        return "RENT"

    return None


def _clean_reference(value: str) -> str | None:
    """
    Keep useful parent description only as REFERENCE ONLY.
    Never feed it into inherited_context.
    """
    n = norm(value).strip("*|:- ")

    if not n:
        return None

    # Contact-only / tiny fragments are not useful parent context.
    if len(n) < 5:
        return None

    return n[:300]


def _split_trailing_assets(piece: str) -> List[str]:
    matches = list(TAIL_ASSET_RE.finditer(piece))

    if not matches:
        return [norm(piece)]

    first = matches[0]
    head = norm(piece[: first.start()])

    chunks = (
        [head]
        if (_looks_like_entity(head) or _numbered_record_like(head))
        else []
    )

    for i, match in enumerate(matches):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(piece)
        )

        tail = norm(piece[match.start() : end])

        if _looks_like_entity(tail) or _numbered_record_like(tail):
            chunks.append(tail)

    return chunks if len(chunks) >= 2 else [norm(piece)]


def _numbered_split(
    textv: str,
) -> Tuple[List[str], List[str], List[str]]:
    marked = INLINE_NUMBERED_RE.sub("\n", textv)
    matches = list(NUMBERED_RE.finditer(marked))

    # First property unnumbered, second starts "2)".
    if len(matches) == 1:
        token = norm(
            marked[matches[0].start() : matches[0].end()]
        )

        mnum = re.match(r"(\d{1,2})", token)
        item_no = int(mnum.group(1)) if mnum else None

        first = norm(
            marked[: matches[0].start()].strip(" -*|\t")
        )
        second = norm(
            marked[matches[0].start() :].strip(" -*|\t")
        )

        if (
            item_no == 2
            and _looks_like_entity(first)
            and _looks_like_entity(second)
        ):
            return [first, second], [], []

        return [], [], []

    if len(matches) < 2:
        return [], [], []

    prefix = marked[: matches[0].start()]
    parts: List[str] = []

    for i, match in enumerate(matches):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(marked)
        )

        piece = norm(
            marked[match.start() : end].strip(" -*|\t")
        )

        if not (
            _looks_like_entity(piece)
            or _numbered_record_like(piece)
        ):
            continue

        for subpiece in _split_trailing_assets(piece):
            if (
                _looks_like_entity(subpiece)
                or _numbered_record_like(subpiece)
            ):
                parts.append(subpiece)

    if len(parts) < 2:
        return [], [], []

    return (
        parts,
        _strict_header_context(prefix),
        [],
    )


def _unit_anchor_split(
    textv: str,
) -> Tuple[List[str], List[str], List[str]]:
    matches = list(UNIT_ANCHOR_RE.finditer(textv))

    if len(matches) < 2:
        return [], [], []

    parts = []

    for i, match in enumerate(matches):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(textv)
        )

        piece = norm(
            textv[match.start() : end].strip(" -*|")
        )

        piece = re.sub(
            r"\s+\d{1,2}\s*$",
            "",
            piece,
        ).strip()

        if _looks_like_entity(piece):
            parts.append(piece)

    if len(parts) < 2:
        return [], [], []

    return (
        parts,
        _strict_header_context(
            textv[: matches[0].start()]
        ),
        [],
    )


def _block_anchor_split(
    textv: str,
) -> Tuple[List[str], List[str], List[str]]:
    matches = list(BLOCK_ANCHOR_RE.finditer(textv))

    if len(matches) < 2:
        return [], [], []

    parts = []

    first_piece = norm(
        textv[: matches[1].start()].strip(" -*|")
    )

    if (
        AREA_RE.search(first_piece)
        and (
            MONEY_RE.search(first_piece)
            or LABELED_MONEY_RE.search(first_piece)
        )
        and PROPERTY_RE.search(first_piece)
    ):
        parts.append(first_piece)
        start = 1
    else:
        start = 0

    for i in range(start, len(matches)):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(textv)
        )

        piece = norm(
            textv[matches[i].start() : end].strip(" -*|")
        )

        if (
            AREA_RE.search(piece)
            and (
                MONEY_RE.search(piece)
                or LABELED_MONEY_RE.search(piece)
            )
        ):
            parts.append(piece)

    if len(parts) < 2:
        return [], [], []

    return (
        parts,
        _strict_header_context(
            textv[: matches[0].start()]
        ),
        [],
    )


def _locality_anchor_split(
    textv: str,
) -> Tuple[List[str], List[str], List[str]]:
    matches = list(LOCALITY_ANCHOR_RE.finditer(textv))

    if len(matches) < 2:
        return [], [], []

    parts = []

    for i, match in enumerate(matches):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(textv)
        )

        piece = norm(
            textv[match.start() : end].strip(" -*|")
        )

        if _fact_count(piece) >= 2:
            parts.append(piece)

    if len(parts) < 2:
        return [], [], []

    # Locality/phase stays in OWN_FACT only.
    return parts, [], []


def _building_split(
    textv: str,
) -> Tuple[List[str], List[str], List[str]]:
    matches = list(BUILDING_RE.finditer(textv))

    if len(matches) < 2:
        return [], [], []

    parts = []

    for i, match in enumerate(matches):
        end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(textv)
        )

        piece = norm(
            textv[match.start() : end].strip(" -*|")
        )

        if _looks_like_entity(piece):
            parts.append(piece)

    if len(parts) < 2:
        return [], [], []

    return (
        parts,
        _strict_header_context(
            textv[: matches[0].start()]
        ),
        [],
    )


def _compact_project_split(
    textv: str,
) -> Tuple[List[str], List[str], List[str]]:
    marked = re.sub(
        r"(?i)(\b(?:CR|CRORE|CRORES|LAC|LAKH|LAKHS)\b)\s+"
        r"(?=[A-Z][A-Za-z0-9&'(). /-]{2,50}\s+(?:"
        r"\d(?:\.\d)?\s*BHK|"
        r"\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT)))",
        r"\1\n",
        textv,
    )

    parts = [
        norm(x)
        for x in marked.splitlines()
        if norm(x)
    ]

    good = [
        x
        for x in parts
        if _looks_like_entity(x)
        and _fact_count(x) >= 2
    ]

    if len(good) < 2:
        return [], [], []

    first_pos = textv.find(good[0])

    prefix = (
        textv[:first_pos]
        if first_pos > 0
        else ""
    )

    return (
        good,
        _strict_header_context(prefix),
        [],
    )


def _mixed_transaction_split(
    textv: str,
) -> Tuple[
    List[Tuple[str, List[str], List[str]]],
    bool,
]:
    """
    Phase 2.3.3:
    Split one WhatsApp bundle when transaction sections change, then split each
    section into repeated configuration entities.

    Example:
      APARTMENT ... ORCHID GARDEN
      FOR SALE
      4.br 3363 sqft Price 11 CR
      3.br 2013 sqft Price ...
      FOR RENT
      4.br 3363 sqft Rent 1.5L
      3.br 2400 sqft Rent 1.25L

    Parent/project text is preserved only as PARENT_CONTEXT_REFERENCE_ONLY.
    It is NOT copied into inherited_context.
    """
    matches = list(
        TRANSACTION_SECTION_RE.finditer(textv)
    )

    if len(matches) < 2:
        return [], False

    labels = [
        _transaction_label(match.group(0))
        for match in matches
    ]

    # Need a real transaction switch, not repeated "for rent" prose.
    if len(set(x for x in labels if x)) < 2:
        return [], False

    global_prefix = norm(
        textv[: matches[0].start()]
    )

    global_reference = []
    cref = _clean_reference(global_prefix)

    if cref:
        global_reference.append(cref)

    output: List[
        Tuple[str, List[str], List[str]]
    ] = []

    for i, match in enumerate(matches):
        section_end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(textv)
        )

        section = textv[
            match.end() : section_end
        ]

        transaction = (
            _transaction_label(
                match.group(0)
            )
        )

        if not transaction:
            continue

        configs = list(
            CONFIG_RE.finditer(section)
        )

        if len(configs) < 2:
            # Keep one entity section only if it itself looks like a valid property.
            candidate = norm(section)

            if _looks_like_entity(candidate):
                output.append(
                    (
                        candidate,
                        [transaction],
                        list(global_reference),
                    )
                )

            continue

        section_prefix = norm(
            section[: configs[0].start()]
        )

        reference = list(global_reference)

        section_ref = _clean_reference(
            section_prefix
        )

        if (
            section_ref
            and section_ref not in reference
        ):
            reference.append(section_ref)

        section_parts = []

        for j, config in enumerate(configs):
            end = (
                configs[j + 1].start()
                if j + 1 < len(configs)
                else len(section)
            )

            piece = norm(
                section[
                    config.start() : end
                ].strip(" -*|")
            )

            if not piece:
                continue

            # Boundary must contain its own config + at least area or money.
            own_config = bool(
                CONFIG_RE.search(piece)
            )
            own_area = bool(
                AREA_RE.search(piece)
            )
            own_money = bool(
                MONEY_RE.search(piece)
                or LABELED_MONEY_RE.search(piece)
            )

            if (
                own_config
                and (own_area or own_money)
            ):
                section_parts.append(piece)

        # Only treat as successfully reconstructed if at least two atomic
        # configuration rows exist in that transaction section.
        if len(section_parts) >= 2:
            for piece in section_parts:
                output.append(
                    (
                        piece,
                        [transaction],
                        list(reference),
                    )
                )
        else:
            # Conservative fallback: if a section cannot be confidently split,
            # abort mixed split and leave original message under review.
            return [], False

    return output, len(output) >= 4


def _requirement_continuation(
    textv: str,
) -> str | None:
    textn = norm(textv)

    if not STRONG_REQUIREMENT_RE.search(textn):
        return None

    if len(NUMBERED_RE.findall(textv)) >= 2:
        return None

    if (
        EXPLICIT_SALE_RE.search(textn)
        and EXPLICIT_RENT_RE.search(textn)
    ):
        return None

    return textn


def entity_complexity(piece: str) -> int:
    s = norm(piece)

    units = len(
        UNIT_ANCHOR_RE.findall(s)
    )

    blocks = len(
        BLOCK_ANCHOR_RE.findall(s)
    )

    numbered = len(
        NUMBERED_RE.findall(s)
    )

    prices = len(
        MONEY_RE.findall(s)
    )

    areas = len(
        AREA_RE.findall(s)
    )

    configs = len(
        CONFIG_RE.findall(s)
    )

    locality = len(
        LOCALITY_ANCHOR_RE.findall(s)
    )

    dense = 0

    if prices >= 2 and (
        areas >= 2
        or configs >= 2
        or units >= 2
        or blocks >= 2
        or locality >= 2
    ):
        dense = min(
            prices,
            max(
                areas,
                configs,
                units,
                blocks,
                locality,
            ),
        )

    return max(
        numbered,
        units,
        blocks,
        dense,
    )


def reconstruct_entities(
    raw: str,
) -> List[EntityBlock]:
    textv = (
        str(raw or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )

    if not textv:
        return []

    merged_requirement = (
        _requirement_continuation(
            textv
        )
    )

    flat: List[
        Tuple[
            str,
            List[str],
            List[str],
            str,
        ]
    ] = []

    if merged_requirement:
        flat = [
            (
                merged_requirement,
                [],
                [],
                "requirement_continuation_merged",
            )
        ]

    else:
        mixed_rows, mixed_ok = (
            _mixed_transaction_split(
                textv
            )
        )

        if mixed_ok:
            flat = [
                (
                    piece,
                    context,
                    reference,
                    "mixed_transaction_config",
                )
                for (
                    piece,
                    context,
                    reference,
                ) in mixed_rows
            ]

        else:
            strategies = [
                (
                    "numbered",
                    _numbered_split,
                ),
                (
                    "unit_anchor",
                    _unit_anchor_split,
                ),
                (
                    "block_anchor",
                    _block_anchor_split,
                ),
                (
                    "locality_anchor",
                    _locality_anchor_split,
                ),
                (
                    "building_project",
                    _building_split,
                ),
                (
                    "compact_project",
                    _compact_project_split,
                ),
            ]

            parts: List[str] = []
            context: List[str] = []
            reference: List[str] = []
            method = "single"

            for name, fn in strategies:
                (
                    found,
                    found_context,
                    found_reference,
                ) = fn(textv)

                if found:
                    parts = found
                    context = (
                        found_context
                    )
                    reference = (
                        found_reference
                    )
                    method = name
                    break

            if not parts:
                parts = [norm(textv)]
                context = []
                reference = []
                method = "single"

            flat = [
                (
                    norm(piece),
                    list(context),
                    list(reference),
                    method,
                )
                for piece in parts
                if len(norm(piece)) >= 12
            ]

    dedup = []
    seen = set()

    for (
        piece,
        context,
        reference,
        method,
    ) in flat:
        key = piece.lower()

        if key in seen:
            continue

        seen.add(key)

        if not (
            _looks_like_entity(piece)
            or _numbered_record_like(piece)
        ):
            if method == "single":
                dedup.append(
                    (
                        piece,
                        context,
                        reference,
                        method,
                    )
                )

            continue

        dedup.append(
            (
                piece,
                context,
                reference,
                method,
            )
        )

    sibling_texts = [
        piece
        for (
            piece,
            _,
            _,
            _,
        ) in dedup
    ]

    out: List[EntityBlock] = []

    for index, (
        piece,
        context,
        reference,
        method,
    ) in enumerate(
        dedup,
        start=1,
    ):
        complexity = (
            entity_complexity(piece)
        )

        out.append(
            EntityBlock(
                index=index,
                own_text=piece,
                inherited_context=context,
                sibling_facts_do_not_copy=[
                    sibling
                    for sibling in sibling_texts
                    if sibling != piece
                ],
                parent_context_reference_only=reference,
                method=method,
                needs_split=(
                    complexity >= 2
                ),
                reason=(
                    f"possible {complexity} properties still combined"
                    if complexity >= 2
                    else None
                ),
            )
        )

    return out
