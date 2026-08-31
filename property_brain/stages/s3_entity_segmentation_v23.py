from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

VERSION = "2.3.1-ANCHOR-PAIRING-CONTINUATION-SAFE"

NUMBERED_RE = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[1-9]️⃣|[①②③④⑤⑥⑦⑧⑨])\s*")
UNIT_ANCHOR_RE = re.compile(r"(?i)(?<![A-Z0-9])(?:B|UNIT|SHOP|OFFICE|PLOT)\s*[-:#]?\s*\d{2,5}\b")
BLOCK_ANCHOR_RE = re.compile(r"(?i)\b[A-Z]\s+BLOCK\b")
LOCALITY_ANCHOR_RE = re.compile(r"(?i)\b(?:DLF\s*PHASE\s*[1-5]|SUSHANT\s*LOK\s*1|SHUSHANT\s*LOK\s*1)\b")
BUILDING_RE = re.compile(r"(?i)\b(?:BUILDING|PROJECT|PROPERTY|OPTION)\s*[-:#]\s*")
CONFIG_RE = re.compile(r"(?i)\b(?:\d(?:\.\d)?\s*(?:BHK|BEDROOMS?)|[2-9](?:/[2-9])+\s*BHK)\b")
PROPERTY_RE = re.compile(
    r"(?i)\b(?:APARTMENT|FLAT|BUILDER\s+FLOOR|FLOOR|VILLA|KOTHI|BUNGALOW|ROW\s+HOUSE|"
    r"PLOT|LAND|OFFICE|SHOWROOM|SHOP|WAREHOUSE|GODOWN|FARMHOUSE|BANQUET|HOTEL|"
    r"GUEST\s*HOUSE|RESTAURANT|CAFE|CLUB|LOUNGE|COMMERCIAL\s+SPACE|COMMERCIAL\s+BUILDING|BANK)\b"
)
AREA_RE = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|SYDS?|YARDS?|GAJ|"
    r"ACRES?|ACER|BIGHA|MTR|MTRS|METER|METERS|SQMT|SQM|SQ\s*MTS?|SQUARE\s+METRES?|SQUARE\s+METERS?)\b"
)
MONEY_RE = re.compile(
    r"(?i)(?:(?:₹|RS\.?|INR)\s*)?\d[\d,]*(?:\.\d+)?\s*(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)\b"
)
LABELED_MONEY_RE = re.compile(
    r"(?i)\b(?:RENT|PRICE|ASKING|DEMAND|SALE\s+PRICE|RATE)\s*[:=@-]?\s*(?:₹|RS\.?|INR)?\s*\d"
)
SALE_RE = re.compile(r"(?i)\b(?:FOR\s+SALE|AVAILABLE\s+FOR\s+SALE|ON\s+SALE|SALE\s+OPTION|RESALE|OUTRIGHT)\b")
RENT_RE = re.compile(r"(?i)\b(?:FOR\s+RENT|AVAILABLE\s+FOR\s+RENT|AVAILABLE\s+ON\s+RENT|FOR\s+LEASE|TO\s+LET|LEASE|RENTAL)\b")
REQ_RE = re.compile(r"(?i)\b(?:REQUIREMENT|REQUIRED|WANTED|LOOKING\s+FOR|NEED|URGENTLY\s+REQUIRED)\b")


@dataclass
class EntityBlock:
    index: int
    own_text: str
    inherited_context: List[str] = field(default_factory=list)
    sibling_facts_do_not_copy: List[str] = field(default_factory=list)
    method: str = "single"
    needs_split: bool = False
    reason: str | None = None


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _transaction_context(prefix: str) -> List[str]:
    p = norm(prefix)
    out = []
    if REQ_RE.search(p):
        out.append("REQUIREMENT")
    elif RENT_RE.search(p) and not SALE_RE.search(p):
        out.append("RENT")
    elif SALE_RE.search(p) and not RENT_RE.search(p):
        out.append("SALE")
    return out


def _safe_context(prefix: str) -> List[str]:
    out = _transaction_context(prefix)

    for line in str(prefix or "").splitlines():
        n = norm(line)
        if not n:
            continue
        if CONFIG_RE.search(n) or AREA_RE.search(n) or MONEY_RE.search(n) or LABELED_MONEY_RE.search(n):
            continue
        if PROPERTY_RE.search(n) and len(n.split()) > 8:
            continue
        if len(n) <= 80 and n not in out:
            out.append(n)

    return out[:5]


def _fact_count(value: str) -> int:
    return (
        int(bool(AREA_RE.search(value)))
        + int(bool(MONEY_RE.search(value) or LABELED_MONEY_RE.search(value)))
        + int(bool(CONFIG_RE.search(value)))
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


def _numbered_split(textv: str):
    marked = re.sub(
        r"(?<![\d.])\s+(?=(?:\d{1,2})[.)]\s+(?=[A-Za-z*]))",
        "\n",
        textv,
    )
    matches = list(NUMBERED_RE.finditer(marked))
    if len(matches) < 2:
        return [], []

    prefix = marked[:matches[0].start()]
    parts = []

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(marked)
        piece = norm(marked[match.start():end].strip(" -*|\t"))
        if _looks_like_entity(piece):
            parts.append(piece)

    return (parts, _safe_context(prefix)) if len(parts) >= 2 else ([], [])


def _unit_anchor_split(textv: str):
    matches = list(UNIT_ANCHOR_RE.finditer(textv))
    if len(matches) < 2:
        return [], []

    prefix = textv[:matches[0].start()]
    parts = []

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(textv)
        piece = norm(textv[match.start():end].strip(" -*|"))
        piece = re.sub(r"\s+\d{1,2}\s*$", "", piece).strip()
        if _looks_like_entity(piece):
            parts.append(piece)

    return (parts, _safe_context(prefix)) if len(parts) >= 2 else ([], [])


def _block_anchor_split(textv: str):
    matches = list(BLOCK_ANCHOR_RE.finditer(textv))
    if len(matches) < 2:
        return [], []

    prefix = textv[:matches[0].start()]
    parts = []

    # Some broker messages put the first area's value before the first block:
    # "... Kalkaji 100 yards K Block Price 8 cr K Block 100 yards 7.50cr ..."
    # Preserve that first property as one entity instead of losing it.
    first_end = matches[1].start()
    first_piece = norm(textv[:first_end].strip(" -*|"))
    if (
        AREA_RE.search(first_piece)
        and (MONEY_RE.search(first_piece) or LABELED_MONEY_RE.search(first_piece))
        and PROPERTY_RE.search(first_piece)
    ):
        parts.append(first_piece)
        start_index = 1
        context = _transaction_context(prefix)
    else:
        start_index = 0
        context = _safe_context(prefix)

    for i in range(start_index, len(matches)):
        match = matches[i]
        end = matches[i + 1].start() if i + 1 < len(matches) else len(textv)
        piece = norm(textv[match.start():end].strip(" -*|"))
        if AREA_RE.search(piece) and (MONEY_RE.search(piece) or LABELED_MONEY_RE.search(piece)):
            parts.append(piece)

    return (parts, context) if len(parts) >= 2 else ([], [])


def _locality_anchor_split(textv: str):
    matches = list(LOCALITY_ANCHOR_RE.finditer(textv))
    if len(matches) < 2:
        return [], []

    parts = []

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(textv)
        piece = norm(textv[match.start():end].strip(" -*|"))
        if _fact_count(piece) >= 2:
            parts.append(piece)

    prefix = textv[:matches[0].start()]
    return (parts, _safe_context(prefix)) if len(parts) >= 2 else ([], [])


def _building_split(textv: str):
    matches = list(BUILDING_RE.finditer(textv))
    if len(matches) < 2:
        return [], []

    parts = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(textv)
        piece = norm(textv[match.start():end].strip(" -*|"))
        if _looks_like_entity(piece):
            parts.append(piece)

    prefix = textv[:matches[0].start()]
    return (parts, _safe_context(prefix)) if len(parts) >= 2 else ([], [])


def _compact_project_split(textv: str):
    marked = re.sub(
        r"(?i)(\b(?:CR|CRORE|CRORES|LAC|LAKH|LAKHS)\b)\s+"
        r"(?=[A-Z][A-Za-z0-9&'(). /-]{2,50}\s+(?:"
        r"\d(?:\.\d)?\s*BHK|\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT)))",
        r"\1\n",
        textv,
    )

    parts = [norm(x) for x in marked.splitlines() if norm(x)]
    good = [x for x in parts if _looks_like_entity(x) and _fact_count(x) >= 2]

    if len(good) < 2:
        return [], []

    first_pos = textv.find(good[0])
    prefix = textv[:first_pos] if first_pos > 0 else ""
    return good, _safe_context(prefix)


def _requirement_continuation(textv: str):
    if not REQ_RE.search(textv):
        return None

    money = len(MONEY_RE.findall(textv))
    areas = len(AREA_RE.findall(textv))
    numbered = len(NUMBERED_RE.findall(textv))

    # A requirement heading followed by explanatory prose is one requirement,
    # not two properties.
    if numbered < 2 and money <= 1 and areas <= 1:
        return norm(textv)

    return None


def entity_complexity(piece: str) -> int:
    s = norm(piece)

    units = len(UNIT_ANCHOR_RE.findall(s))
    blocks = len(BLOCK_ANCHOR_RE.findall(s))
    numbered = len(NUMBERED_RE.findall(s))
    prices = len(MONEY_RE.findall(s))
    areas = len(AREA_RE.findall(s))
    configs = len(CONFIG_RE.findall(s))
    locality = len(LOCALITY_ANCHOR_RE.findall(s))

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
            max(areas, configs, units, blocks, locality),
        )

    return max(numbered, units, blocks, dense)


def reconstruct_entities(raw: str) -> List[EntityBlock]:
    textv = (
        str(raw or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )

    if not textv:
        return []

    merged_requirement = _requirement_continuation(textv)

    if merged_requirement:
        flat = [
            (
                merged_requirement,
                [],
                "requirement_continuation_merged",
            )
        ]
    else:
        strategies = [
            ("numbered", _numbered_split),
            ("unit_anchor", _unit_anchor_split),
            ("block_anchor", _block_anchor_split),
            ("locality_anchor", _locality_anchor_split),
            ("building_project", _building_split),
            ("compact_project", _compact_project_split),
        ]

        parts = []
        context = []
        method = "single"

        for name, fn in strategies:
            found, found_context = fn(textv)
            if found:
                parts = found
                context = found_context
                method = name
                break

        if not parts:
            parts = [norm(textv)]
            context = []
            method = "single"

        flat = [
            (norm(piece), list(context), method)
            for piece in parts
            if len(norm(piece)) >= 12
        ]

    dedup = []
    seen = set()

    for piece, context, method in flat:
        key = piece.lower()

        if key in seen:
            continue

        seen.add(key)

        if not _looks_like_entity(piece):
            # Keep only the original unsplit message for review.
            # Never manufacture an artificial price-only/area-only entity.
            if method == "single":
                dedup.append((piece, context, method))
            continue

        dedup.append((piece, context, method))

    sibling_texts = [piece for piece, _, _ in dedup]
    out: List[EntityBlock] = []

    for index, (piece, context, method) in enumerate(dedup, start=1):
        complexity = entity_complexity(piece)

        out.append(
            EntityBlock(
                index=index,
                own_text=piece,
                inherited_context=context,
                sibling_facts_do_not_copy=[
                    x for x in sibling_texts
                    if x != piece
                ],
                method=method,
                needs_split=complexity >= 2,
                reason=(
                    f"possible {complexity} properties still combined"
                    if complexity >= 2
                    else None
                ),
            )
        )

    return out
