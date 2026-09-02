from pathlib import Path
from datetime import datetime
import py_compile
import shutil
import sys

TARGET = Path("alliance_property_brain_foundation_v1.py")

if not TARGET.exists():
    raise SystemExit("ERROR: Run this from the property-intelligence-backend repository root.")

src = TARGET.read_text(encoding="utf-8")

EXPECTED_VERSION = 'VERSION = "1.9.28-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"'
EXPECTED_MODE = 'MODE = "GOLD_LAB_JS_PARSE_REPAIR_1_9Y2"'
if EXPECTED_VERSION not in src or EXPECTED_MODE not in src:
    raise SystemExit(
        "ERROR: Current main is not the verified 1.9Y2 baseline. Do not apply blindly."
    )

required_tokens = {
    "1.9V project+BHK splitter": "FOUNDATION_1_9V_PROJECT_BHK_INVENTORY_SPLIT",
    "1.9W shared contact recovery": "FOUNDATION_1_9W",
    "1.9X mixed pin+asset splitter": "FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT",
    "1.9Y same-source split/save flow": "loadNextInSourceOrGlobal",
}
missing = [name for name, token in required_tokens.items() if token not in src]
if missing:
    raise SystemExit("ERROR: Existing accumulated fix missing before install: " + ", ".join(missing))

MARKER = "# FOUNDATION_1_9Z_BUILDER_FLOOR_OPTION_SPLIT"
if MARKER in src:
    print("FOUNDATION_1_9Z_ALREADY_INSTALLED")
    sys.exit(0)

splitter = r'''
# FOUNDATION_1_9Z_BUILDER_FLOOR_OPTION_SPLIT
def _v19z_builder_floor_option_split(text_value: str):
    raw = str(text_value or "")
    if not raw.strip():
        return None

    lines = _line_ranges(raw)
    if not lines:
        return None

    def clean(line):
        return re.sub(r"[*_`]+", "", str(line or "")).strip()

    floor_anchor_re = re.compile(
        r"^\s*(?:✅|☑️?|✔️?|▪️?|•)?\s*"
        r"(?:(?:GROUND|LOWER\s+GROUND|UPPER\s+GROUND|BASEMENT|"
        r"\d{1,2}(?:ST|ND|RD|TH))\s+FLOOR)\b",
        re.I,
    )

    anchors = []
    for idx, (start, end, line) in enumerate(lines):
        c = clean(line)
        if floor_anchor_re.search(c):
            anchors.append((idx, start, end, c))

    if len(anchors) < 2:
        return None

    preamble = raw[:anchors[0][1]].strip()

    if not re.search(
        r"\b(?:BUILDER\s+FLOORS?|INDEPENDENT\s+BUILDER\s+FLOORS?|"
        r"INDEPENDENT\s+FLOORS?|FLOORS?\s+FOR\s+(?:SALE|RENT))\b",
        preamble,
        re.I,
    ):
        return None

    inherited_tx = None
    if re.search(r"\bFOR\s+SALE\b", preamble, re.I):
        inherited_tx = "SALE"
    elif re.search(r"\bFOR\s+(?:RENT|LEASE)\b", preamble, re.I):
        inherited_tx = "RENT"

    inherited_locality = None
    if lines:
        first = clean(lines[0][2])
        if "|" in first:
            candidate = first.split("|", 1)[0].strip(" -–—")
            if candidate:
                inherited_locality = candidate

    shared_plot_area = None
    m = re.search(
        r"\bPLOT\s+SIZE\s*:\s*(\d+(?:\.\d+)?)\s*"
        r"(SQ\.?\s*YARDS?|SQ\.?\s*YDS?|SQYDS?|SQYD|YARDS?|GAJ)\b",
        preamble,
        re.I,
    )
    if m:
        shared_plot_area = {
            "value": float(m.group(1)),
            "unit": m.group(2),
            "role": "PLOT_AREA",
            "evidence": m.group(0),
            "provenance": "INHERITED_FROM_SOURCE_PREAMBLE",
        }

    footer_start = len(raw)
    footer_re = re.compile(
        r"^(?:💰\s*CHEQUE\b|CHEQUE\s+COMPONENT\b|"
        r"FOR\s+MORE\s+DETAILS\b|FOR\s+SITE\s+VISIT\b|"
        r"CONTACT\b|CALL\b)",
        re.I,
    )
    for idx in range(anchors[-1][0] + 1, len(lines)):
        ls, _le, line = lines[idx]
        if footer_re.search(clean(line)):
            footer_start = ls
            break

    children = []
    ranges = []

    for n, (_line_idx, start, _end, heading) in enumerate(anchors):
        end = anchors[n + 1][1] if n + 1 < len(anchors) else footer_start

        while end > start and raw[end - 1] in "\r\n \t":
            end -= 1
        if end <= start:
            return None

        exact_block = raw[start:end]
        child_text = exact_block.strip()
        if not child_text:
            return None

        left_trim = len(exact_block) - len(exact_block.lstrip())
        exact_start = start + left_trim
        exact_end = exact_start + len(child_text)

        if raw[exact_start:exact_end] != child_text:
            return None

        proposal = _v16_enrich_proposal(child_text)
        proposal.setdefault("context_provenance", {})
        proposal["context_provenance"]["atomic_boundary"] = (
            "BUILDER_FLOOR_OPTION_SOURCE_TEXT_1_9Z"
        )

        tx = str(proposal.get("transaction_type_hint") or "").strip().upper()
        if inherited_tx and tx in {"", "UNKNOWN", "AMBIGUOUS"}:
            proposal["transaction_type_hint"] = inherited_tx
            proposal["context_provenance"]["transaction_type_hint"] = (
                "INHERITED_FROM_SOURCE_PREAMBLE"
            )

        if inherited_locality and not proposal.get("locality_hint"):
            proposal["locality_hint"] = inherited_locality
            proposal["context_provenance"]["locality_hint"] = (
                "INHERITED_FROM_SOURCE_PREAMBLE"
            )

        if shared_plot_area:
            areas = list(proposal.get("areas") or [])
            if not any(
                isinstance(a, dict)
                and str(a.get("role") or "").upper() == "PLOT_AREA"
                for a in areas
            ):
                areas.append(dict(shared_plot_area))
            proposal["areas"] = areas

        children.append({
            "child_order": n + 1,
            "start_offset": exact_start,
            "end_offset": exact_end,
            "text": child_text,
            "proposal": proposal,
            "context": {
                "boundary_strategy": "BUILDER_FLOOR_OPTION_1_9Z",
                "heading": heading,
                "context_is_source_grounded": True,
                "inherited_transaction": inherited_tx,
                "inherited_locality": inherited_locality,
                "shared_plot_area": shared_plot_area,
            },
        })
        ranges.append((exact_start, exact_end))

    if len(children) < 2:
        return None

    previous_end = -1
    for child in children:
        s = int(child["start_offset"])
        e = int(child["end_offset"])
        if s < previous_end or raw[s:e] != child["text"]:
            return None
        previous_end = e

    return {
        "status": "PASS",
        "reason": "Repeated explicit builder-floor options form atomic property blocks.",
        "boundary_strategy": "BUILDER_FLOOR_OPTION_1_9Z",
        "children": children,
        "shared_context": _context_from_ranges(raw, ranges),
        "source_grounded": True,
        "human_confirmation_required": True,
    }

'''

anchor = "\ndef automatic_atomic_split(text_value: str) -> Dict[str, Any]:\n"
if anchor not in src:
    raise SystemExit("ERROR: automatic_atomic_split() anchor not found.")

src = src.replace(anchor, "\n" + splitter + anchor, 1)

old = '''def automatic_atomic_split(text_value: str) -> Dict[str, Any]:
    raw = str(text_value or "")

    # Foundation 1.9V: compact project-name + BHK broker inventories.
'''
new = '''def automatic_atomic_split(text_value: str) -> Dict[str, Any]:
    raw = str(text_value or "")

    # Foundation 1.9Z: repeated explicit builder-floor options.
    v19z = _v19z_builder_floor_option_split(raw)
    if v19z is not None:
        return v19z

    # Foundation 1.9V: compact project-name + BHK broker inventories.
'''
if old not in src:
    raise SystemExit("ERROR: automatic_atomic_split() baseline body not found.")
src = src.replace(old, new, 1)

src = src.replace(
    EXPECTED_VERSION,
    'VERSION = "1.9.29-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    EXPECTED_MODE,
    'MODE = "RESTORED_ALL_GOLD_FIXES_PLUS_FLOOR_SPLIT_1_9Z"',
    1,
)

backup = TARGET.with_name(
    TARGET.name + ".before-1_9Z-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
)
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)

    final = TARGET.read_text(encoding="utf-8")
    for name, token in required_tokens.items():
        if token not in final:
            raise RuntimeError("Prior fix lost: " + name)

    if MARKER not in final:
        raise RuntimeError("1.9Z splitter missing after write.")

    inside_script = False
    for line_no, line in enumerate(final.splitlines(), start=1):
        low = line.lower()
        if "<script" in low:
            inside_script = True
        if inside_script and line.lstrip().startswith("#"):
            raise RuntimeError(
                f"Invalid Python-style # comment inside Gold Lab JavaScript at line {line_no}"
            )
        if "</script>" in low:
            inside_script = False

except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9Z_INSTALL_PASS")
print("Verified baseline: Foundation 1.9.28 / 1.9Y2")
print("Existing accumulated fixes: PRESERVED")
print("New South City builder-floor splitter: INSTALLED")
print("Expected split: 2 children")
print("  1) 3rd Floor + Full Basement")
print("  2) 4th Floor with Exclusive Terrace")
print("Shared South City-1 / 360 Sq. Yards / SALE context: preserved")
print("Gold Lab JS regression guard: PASS")
print("Version: 1.9.29-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: RESTORED_ALL_GOLD_FIXES_PLUS_FLOOR_SPLIT_1_9Z")
print("Backup:", backup)
