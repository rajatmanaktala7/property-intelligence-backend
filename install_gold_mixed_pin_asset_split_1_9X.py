from pathlib import Path
from datetime import datetime
import py_compile
import shutil
import sys

TARGET = Path("alliance_property_brain_foundation_v1.py")
if not TARGET.exists():
    raise SystemExit("ERROR: alliance_property_brain_foundation_v1.py not found. Run from repo root.")

src = TARGET.read_text(encoding="utf-8")
if 'VERSION = "1.9.25-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9.25 baseline.")
if "# FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT" in src:
    print("FOUNDATION_1_9X_ALREADY_INSTALLED")
    sys.exit(0)

insert_before = "\ndef automatic_atomic_split(text_value: str) -> Dict[str, Any]:\n"
pos = src.find(insert_before)
if pos < 0:
    raise SystemExit("ERROR: automatic_atomic_split anchor not found.")

PATCH = '\n# FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT\ndef _v19x_mixed_pin_asset_heading_split(text_value: str):\n    # Mixed inventories: repeated 📍 headings plus a later strong standalone\n    # residential asset heading such as "🏡 Luxury Kothi – Sector 72".\n    raw = str(text_value or "")\n    if not raw.strip():\n        return None\n\n    lines = _line_ranges(raw)\n    if not lines:\n        return None\n\n    def clean(line):\n        s = str(line or "").strip()\n        return re.sub(r"[*_`]+", "", s).strip()\n\n    def is_pin_heading(line):\n        return bool(re.match(r"^\\s*📍\\s*\\S", str(line or "")))\n\n    strong_asset_re = re.compile(\n        r"^\\s*(?:🏡|🏠|🏢|🏘️?|🏚️?)\\s*"\n        r"(?:LUXURY\\s+|PREMIUM\\s+|INDEPENDENT\\s+)?"\n        r"(?:KOTHI|BUNGALOW|VILLA|HOUSE|APARTMENT|FLAT)\\b",\n        re.I,\n    )\n\n    def is_strong_asset_heading(line):\n        return bool(strong_asset_re.search(str(line or "")))\n\n    def is_footer_line(line):\n        original = str(line or "").strip()\n        c = clean(original)\n        if not c:\n            return False\n        if re.match(r"^\\s*(?:⭐|📞|☎️?|📲)", original):\n            return True\n        if PHONE_RE.search(c) or V18_FOOTER_PHONE_RE.search(original):\n            return True\n        return bool(re.search(\n            r"\\b(?:BEST DEALS?|PRIME LOCATIONS?|GENUINE PROPERTIES|"\n            r"INVESTMENT OPPORTUNITIES|CONTACT|CALL|SITE VISITS?|FOR MORE DETAILS)\\b",\n            c,\n            re.I,\n        ))\n\n    pin_indexes = [\n        i for i, (_s, _e, line) in enumerate(lines)\n        if is_pin_heading(line)\n    ]\n    if len(pin_indexes) < 2:\n        return None\n\n    first_pin_idx = pin_indexes[0]\n    anchors = []\n    pin_count = 0\n    asset_count = 0\n\n    for idx, (start, end, line) in enumerate(lines):\n        if idx < first_pin_idx:\n            continue\n        if is_pin_heading(line):\n            anchors.append((idx, start, end, "PIN"))\n            pin_count += 1\n        elif is_strong_asset_heading(line):\n            anchors.append((idx, start, end, "ASSET"))\n            asset_count += 1\n\n    if pin_count < 2 or asset_count < 1 or len(anchors) < 3:\n        return None\n\n    anchors.sort(key=lambda x: x[1])\n\n    footer_start = len(raw)\n    last_anchor_idx = anchors[-1][0]\n    for idx in range(last_anchor_idx + 1, len(lines)):\n        ls, _le, line = lines[idx]\n        if is_footer_line(line):\n            footer_start = ls\n            break\n\n    preamble = raw[:anchors[0][1]]\n    inherited_tx = None\n    if re.search(\n        r"\\b(?:FOR\\s+SALE|SALE\\s+AVAILABLE|PROPERTIES?\\s+FOR\\s+SALE)\\b",\n        preamble,\n        re.I,\n    ):\n        inherited_tx = "SALE"\n    elif re.search(\n        r"\\b(?:FOR\\s+RENT|RENTAL\\s+AVAILABLE|PROPERTIES?\\s+FOR\\s+RENT)\\b",\n        preamble,\n        re.I,\n    ):\n        inherited_tx = "RENT"\n\n    children = []\n    ranges = []\n\n    for n, item in enumerate(anchors):\n        _idx, start, _line_end, kind = item\n        end = anchors[n + 1][1] if n + 1 < len(anchors) else footer_start\n\n        while end > start and raw[end - 1] in "\\r\\n \\t":\n            end -= 1\n        if end <= start:\n            return None\n\n        exact = raw[start:end]\n        child_text = exact.strip()\n        if not child_text:\n            return None\n\n        left = len(exact) - len(exact.lstrip())\n        exact_start = start + left\n        exact_end = exact_start + len(child_text)\n        if raw[exact_start:exact_end] != child_text:\n            return None\n\n        proposal = _v16_enrich_proposal(child_text)\n        proposal.setdefault("context_provenance", {})\n        proposal["context_provenance"]["atomic_boundary"] = (\n            "MIXED_PIN_ASSET_HEADING_SOURCE_TEXT_1_9X"\n        )\n\n        current_tx = str(\n            proposal.get("transaction_type_hint") or ""\n        ).strip().upper()\n        if inherited_tx and current_tx in {"", "UNKNOWN", "AMBIGUOUS"}:\n            proposal["transaction_type_hint"] = inherited_tx\n            proposal["context_provenance"]["transaction_type_hint"] = (\n                "INHERITED_FROM_SOURCE_PREAMBLE"\n            )\n\n        children.append({\n            "child_order": n + 1,\n            "start_offset": exact_start,\n            "end_offset": exact_end,\n            "text": child_text,\n            "proposal": proposal,\n            "context": {\n                "boundary_strategy": "MIXED_PIN_ASSET_HEADING_1_9X",\n                "heading_kind": kind,\n                "context_is_source_grounded": True,\n                "inherited_transaction": inherited_tx,\n            },\n        })\n        ranges.append((exact_start, exact_end))\n\n    previous_end = -1\n    for child in children:\n        s = int(child["start_offset"])\n        e = int(child["end_offset"])\n        if s < previous_end or raw[s:e] != child["text"]:\n            return None\n        previous_end = e\n\n    return {\n        "status": "PASS",\n        "reason": (\n            "Mixed pin headings plus strong standalone residential asset "\n            "headings form atomic property blocks."\n        ),\n        "boundary_strategy": "MIXED_PIN_ASSET_HEADING_1_9X",\n        "children": children,\n        "shared_context": _context_from_ranges(raw, ranges),\n        "source_grounded": True,\n        "human_confirmation_required": True,\n    }\n'
src = src[:pos] + PATCH + src[pos:]

old_call = """    # Foundation 1.9V: compact project-name + BHK broker inventories.
    v19v = _v19v_project_bhk_inventory_split(raw)
    if v19v is not None:
        return v19v

    v19f = _v19f_inline_numbered_split(raw)
"""

new_call = """    # Foundation 1.9V: compact project-name + BHK broker inventories.
    v19v = _v19v_project_bhk_inventory_split(raw)
    if v19v is not None:
        return v19v

    # Foundation 1.9X: mixed pin headings plus standalone asset headings.
    v19x = _v19x_mixed_pin_asset_heading_split(raw)
    if v19x is not None:
        return v19x

    v19f = _v19f_inline_numbered_split(raw)
"""

if old_call not in src:
    raise SystemExit("ERROR: automatic splitter precedence block not found.")
src = src.replace(old_call, new_call, 1)

src = src.replace(
    'VERSION = "1.9.25-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    'VERSION = "1.9.26-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    'MODE = "SHARED_TAIL_CONTACT_RECOVERY_1_9W"',
    'MODE = "MIXED_PIN_ASSET_ATOMIC_SPLIT_1_9X"',
    1,
)

backup = TARGET.with_name(
    TARGET.name + ".before-1_9X-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
)
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)

    ns = {}
    exec(compile(src, str(TARGET), "exec"), ns)

    sample = """🏡✨ PREMIUM PROPERTIES FOR SALE – NOIDA ✨🏡

🔥 Exclusive Residential & Commercial Properties Available 🔥

📍 Sector 108, Noida
✅ 450 Sq. Mtr. (24 × 25 Mtr.)
✅ CC Plot
✅ Wide Road
💎 Premium Location

📍 Sector 51, Noida – B Block
✅ 450 Sq. Mtr.
✅ 24 Mtr. Wide Road + Park
✅ West / North Facing

📍 Sector 51, Noida – D Block
✅ 450 Sq. Mtr.
✅ CC Plot
✅ 18 Mtr. Wide Road

📍 Sector 71 / 70 / 61, Noida
✅ 450 Sq. Mtr. Plots Available

📍 Sector 72, Noida
🏡 300 Sq. Mtr. CC Plot
✅ Park + North-East Facing
💰 ₹9.25 Cr (Max Cheque)

🏡 Luxury Kothi – Sector 72
✅ 300 Sq. Mtr.
✅ Green Belt Facing
✅ Stilt + 3 Floors
✅ Simplex with Lift
💰 ₹12 Cr

⭐ Best Deals • Prime Locations • Genuine Properties • Investment Opportunities

📞 Sagar Properties
98117 20236
96504 01674"""

    result = ns["_v19x_mixed_pin_asset_heading_split"](sample)
    if not result or result.get("status") != "PASS":
        raise RuntimeError("1.9X regression failed: splitter did not PASS.")

    children = result.get("children") or []
    if len(children) != 6:
        raise RuntimeError(f"1.9X regression failed: expected 6 children, got {len(children)}.")

    expected_starts = [
        "📍 Sector 108, Noida",
        "📍 Sector 51, Noida – B Block",
        "📍 Sector 51, Noida – D Block",
        "📍 Sector 71 / 70 / 61, Noida",
        "📍 Sector 72, Noida",
        "🏡 Luxury Kothi – Sector 72",
    ]

    for idx, expected in enumerate(expected_starts):
        if not children[idx]["text"].startswith(expected):
            raise RuntimeError(
                f"1.9X regression failed at child {idx+1}: "
                f"expected {expected!r}, got {children[idx]['text'][:80]!r}"
            )

    if "Luxury Kothi" in children[4]["text"]:
        raise RuntimeError("1.9X regression failed: Luxury Kothi leaked into child 5.")

    if "Best Deals" in children[5]["text"] or "Sagar Properties" in children[5]["text"]:
        raise RuntimeError("1.9X regression failed: footer leaked into child 6.")

    shared_text = "\n".join(result.get("shared_context") or [])
    for required in (
        "PREMIUM PROPERTIES FOR SALE",
        "Best Deals",
        "Sagar Properties",
        "98117 20236",
        "96504 01674",
    ):
        if required not in shared_text:
            raise RuntimeError(
                f"1.9X regression failed: shared context missing {required!r}."
            )

    contacts = ns["_v18_shared_source_contacts"](sample)
    phones = sorted(x.get("phone") for x in contacts)
    expected_phones = sorted(["+919811720236", "+919650401674"])
    if phones != expected_phones:
        raise RuntimeError(
            f"1.9X/1.9W contact regression failed: expected {expected_phones}, got {phones}"
        )

    if not all(x.get("provenance") == "SOURCE_SHARED_CONTACT" for x in contacts):
        raise RuntimeError("1.9X/1.9W provenance regression failed.")

except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9X_INSTALL_PASS")
print("Atomic children: 6")
print("Child 5 ends at: ₹9.25 Cr (Max Cheque)")
print("Child 6 starts at: 🏡 Luxury Kothi – Sector 72")
print("Shared contacts preserved: +919811720236, +919650401674")
print("Version: 1.9.26-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: MIXED_PIN_ASSET_ATOMIC_SPLIT_1_9X")
print("Backup:", backup)
