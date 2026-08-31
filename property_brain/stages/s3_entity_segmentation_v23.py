from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List

VERSION = "2.3.0-SHARED-ENTITY-PURITY"

NUMBERED_RE = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[1-9]️⃣|[①②③④⑤⑥⑦⑧⑨])\s*")
BUILDING_RE = re.compile(r"(?i)\b(?:BUILDING|PROJECT|PROPERTY|OPTION)\s*[-:#]\s*")
CONFIG_RE = re.compile(r"(?i)\b\d(?:\.\d)?\s*(?:BHK|BEDROOMS?)\b")
PROPERTY_RE = re.compile(
    r"(?i)\b(?:APARTMENT|FLAT|BUILDER\s+FLOOR|VILLA|KOTHI|BUNGALOW|ROW\s+HOUSE|"
    r"PLOT|LAND|OFFICE|SHOWROOM|SHOP|WAREHOUSE|GODOWN|FARMHOUSE|BANQUET|HOTEL|"
    r"GUEST\s*HOUSE|RESTAURANT|CAFE|CLUB|LOUNGE|COMMERCIAL\s+SPACE|COMMERCIAL\s+BUILDING)\b"
)
AREA_RE = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|GAJ|"
    r"ACRES?|SQMT|SQM|SQ\s*MTS?|SQUARE\s+METRES?|SQUARE\s+METERS?)\b"
)
PRICE_RE = re.compile(
    r"(?i)(?:₹|RS\.?|INR|\b(?:RENT|PRICE|ASKING|DEMAND|SALE\s+PRICE)\b)"
    r"\s*[:=@-]?\s*\d[\d,]*(?:\.\d+)?\s*(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)?"
)
SALE_RE = re.compile(r"(?i)\b(?:FOR\s+SALE|AVAILABLE\s+FOR\s+SALE|ON\s+SALE|SALE\s+OPTION|RESALE|OUTRIGHT)\b")
RENT_RE = re.compile(r"(?i)\b(?:FOR\s+RENT|AVAILABLE\s+FOR\s+RENT|AVAILABLE\s+ON\s+RENT|FOR\s+LEASE|TO\s+LET|LEASE)\b")
REQ_RE = re.compile(r"(?i)\b(?:REQUIREMENT|REQUIRED|WANTED|LOOKING\s+FOR|NEED|URGENTLY\s+REQUIRED)\b")
SECTION_RE = re.compile(
    r"(?i)(?=(?:^|\n)\s*(?:EXCLUSIVE\s+DEALS?\s+ON\s+SALE|AVAILABLE\s+FOR\s+SALE|FOR\s+SALE|"
    r"AVAILABLE\s+FOR\s+RENT|FOR\s+RENT|FOR\s+LEASE|REQUIREMENTS?|REQUIRED)\b)"
)

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

def _context_prefix(prefix: str) -> List[str]:
    p = norm(prefix)
    if not p:
        return []
    hints: List[str] = []
    if REQ_RE.search(p):
        hints.append("REQUIREMENT")
    elif RENT_RE.search(p):
        hints.append("RENT")
    elif SALE_RE.search(p):
        hints.append("SALE")
    for line in str(prefix or "").splitlines():
        n = norm(line)
        if not n:
            continue
        if CONFIG_RE.search(n) or AREA_RE.search(n) or PRICE_RE.search(n):
            continue
        if len(n) <= 100 and n not in hints:
            hints.append(n)
    return hints[:8]

def _looks_like_entity(piece: str) -> bool:
    s = str(piece or "")
    return bool(PROPERTY_RE.search(s) or CONFIG_RE.search(s)) and bool(
        AREA_RE.search(s) or PRICE_RE.search(s) or SALE_RE.search(s) or RENT_RE.search(s) or REQ_RE.search(s)
    )

def entity_complexity(piece: str) -> int:
    s = str(piece or "")
    numbered = len(NUMBERED_RE.findall(s))
    building = len(BUILDING_RE.findall(s))
    areas = len(AREA_RE.findall(s))
    prices = len(PRICE_RE.findall(s))
    configs = len(CONFIG_RE.findall(s))
    positive = [x for x in (areas, prices, configs) if x > 0]
    paired = min(positive) if len(positive) >= 2 else 0
    return max(numbered, building, paired)

def _numbered_split(textv: str):
    marked = re.sub(r"(?<![\d.])\s+(?=(?:\d{1,2})[.)]\s+(?=[A-Za-z*]))", "\n", textv)
    matches = list(NUMBERED_RE.finditer(marked))
    if len(matches) < 2:
        return [], []
    context = _context_prefix(marked[:matches[0].start()])
    parts = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(marked)
        piece = norm(marked[m.start():end].strip("* \t"))
        if _looks_like_entity(piece):
            parts.append(piece)
    return (parts, context) if len(parts) >= 2 else ([], [])

def _building_split(textv: str):
    matches = list(BUILDING_RE.finditer(textv))
    if len(matches) < 2:
        return [], []
    context = _context_prefix(textv[:matches[0].start()])
    parts = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(textv)
        piece = norm(textv[m.start():end])
        if _looks_like_entity(piece):
            parts.append(piece)
    return (parts, context) if len(parts) >= 2 else ([], [])

def _compact_project_split(textv: str):
    marked = re.sub(
        r"(?i)(\b(?:CR|CRORE|CRORES|LAC|LAKH|LAKHS)\b)\s+"
        r"(?=[A-Z][A-Za-z0-9&'(). /-]{2,50}\s+(?:\d(?:\.\d)?\s*BHK|\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT)))",
        r"\1\n",
        textv,
    )
    parts = [norm(x) for x in marked.splitlines() if norm(x)]
    good = [x for x in parts if _looks_like_entity(x)]
    if len(good) < 2:
        return [], []
    first_pos = textv.find(good[0])
    prefix = textv[:first_pos] if first_pos > 0 else ""
    return good, _context_prefix(prefix)

def _section_split(textv: str) -> List[str]:
    parts = [x.strip() for x in SECTION_RE.split(textv) if norm(x)]
    return parts if len(parts) >= 2 else [textv]

def reconstruct_entities(raw: str) -> List[EntityBlock]:
    textv = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not textv:
        return []
    groups = []
    for section in _section_split(textv):
        parts, context = _numbered_split(section)
        method = "numbered"
        if not parts:
            parts, context = _building_split(section)
            method = "building_project"
        if not parts:
            parts, context = _compact_project_split(section)
            method = "compact_project"
        if not parts:
            parts, context, method = [norm(section)], [], "single"
        groups.append((parts, context, method))
    flat, seen = [], set()
    for parts, context, method in groups:
        for piece in parts:
            piece = norm(piece)
            if len(piece) < 12:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            flat.append((piece, list(context), method))
    sibling_texts = [p for p, _, _ in flat]
    out: List[EntityBlock] = []
    for index, (piece, context, method) in enumerate(flat, start=1):
        complexity = entity_complexity(piece)
        out.append(EntityBlock(
            index=index,
            own_text=piece,
            inherited_context=context,
            sibling_facts_do_not_copy=[x for x in sibling_texts if x != piece],
            method=method,
            needs_split=complexity >= 2,
            reason=f"possible {complexity} properties still combined" if complexity >= 2 else None,
        ))
    return out
