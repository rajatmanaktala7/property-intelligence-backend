from pathlib import Path
from datetime import datetime
import py_compile, shutil, sys

TARGET = Path("alliance_property_brain_foundation_v1.py")
if not TARGET.exists():
    raise SystemExit("ERROR: run from repository root")
src = TARGET.read_text(encoding="utf-8")
if 'VERSION = "1.9.23-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9.23 baseline")
if "# FOUNDATION_1_9V_PROJECT_BHK_INVENTORY_SPLIT" in src:
    print("FOUNDATION_1_9V_ALREADY_INSTALLED")
    sys.exit(0)

anchor = "def automatic_atomic_split(text_value: str) -> Dict[str, Any]:\n"
if anchor not in src:
    raise SystemExit("ERROR: automatic_atomic_split anchor missing")

patch = r'''
# FOUNDATION_1_9V_PROJECT_BHK_INVENTORY_SPLIT
def _v19v_project_bhk_inventory_split(text_value: str):
    raw = str(text_value or "")
    if not raw.strip():
        return None
    lines = _line_ranges(raw)
    anchors = []

    def clean(line):
        return re.sub(r"[*_`]+", "", str(line or "")).strip()

    def footerish(line):
        c = clean(line)
        return bool(PHONE_RE.search(c) or re.search(
            r"\b(?:CONTACT|CALL|FOR MORE DETAILS|SITE VISIT|BROKER|REALTOR|REALTY|ASSOCIATES)\b",
            c, re.I))

    for idx, (start, end, line) in enumerate(lines):
        c = clean(line)
        if not c or footerish(c):
            continue
        same_bhk = bool(re.search(r"\b\d+(?:\.\d+)?\s*BHK\b", c, re.I))
        named_bhk = same_bhk and bool(re.match(r"^[A-Za-z][A-Za-z0-9 .&()/'-]{2,100}", c))
        first_header = False
        if re.search(r"\b(?:SALE|RENT)\b", c, re.I) and re.search(r"\bSIZE\b", c, re.I):
            if idx + 1 < len(lines):
                first_header = bool(re.search(r"\b\d+(?:\.\d+)?\s*BHK\b", clean(lines[idx+1][2]), re.I))
        if named_bhk or first_header:
            anchors.append((idx, start, end, c, same_bhk))

    if len(anchors) < 3 or sum(1 for x in anchors if x[4]) < 2:
        return None

    footer_start = len(raw)
    for idx in range(anchors[-1][0] + 1, len(lines)):
        ls, le, line = lines[idx]
        c = clean(line)
        if c and footerish(c):
            footer_start = ls
            break

    children = []
    for n, item in enumerate(anchors):
        start = item[1]
        end = anchors[n+1][1] if n + 1 < len(anchors) else footer_start
        exact = raw[start:end]
        if not exact.strip():
            return None
        proposal = _v16_enrich_proposal(exact.strip())
        proposal.setdefault("context_provenance", {})
        proposal["context_provenance"]["atomic_boundary"] = "PROJECT_BHK_INVENTORY_SOURCE_TEXT_1_9V"
        children.append({
            "child_order": n + 1,
            "start_offset": start,
            "end_offset": end,
            "text": exact.strip(),
            "proposal": proposal,
        })

    shared = []
    if anchors[0][1] > 0 and raw[:anchors[0][1]].strip():
        shared.append(raw[:anchors[0][1]].strip())
    if footer_start < len(raw) and raw[footer_start:].strip():
        shared.append(raw[footer_start:].strip())

    return {
        "status": "PASS",
        "reason": "Repeated project + BHK inventory headings form atomic property blocks.",
        "boundary_strategy": "PROJECT_BHK_INVENTORY_1_9V",
        "children": children,
        "shared_context": "\n\n".join(shared),
        "source_grounded": True,
        "human_confirmation_required": True,
    }


'''
src = src.replace(anchor, patch + anchor, 1)

old = '    raw = str(text_value or "")\n    v19f = _v19f_inline_numbered_split(raw)\n'
new = '    raw = str(text_value or "")\n\n    # Foundation 1.9V: compact project-name + BHK broker inventories.\n    v19v = _v19v_project_bhk_inventory_split(raw)\n    if v19v is not None:\n        return v19v\n\n    v19f = _v19f_inline_numbered_split(raw)\n'
if old not in src:
    raise SystemExit("ERROR: automatic_atomic_split body anchor missing")
src = src.replace(old, new, 1)
src = src.replace('VERSION = "1.9.23-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"', 'VERSION = "1.9.24-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"', 1)
src = src.replace('MODE = "WHATSAPP_SENDER_JID_CONTACT_RECOVERY_1_9U"', 'MODE = "PROJECT_BHK_ATOMIC_SPLIT_1_9V"', 1)

backup = TARGET.with_name(TARGET.name + ".before-1_9V-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(TARGET, backup)
try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9V_INSTALL_PASS")
print("Backup:", backup)
print("Version: 1.9.24-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: PROJECT_BHK_ATOMIC_SPLIT_1_9V")
