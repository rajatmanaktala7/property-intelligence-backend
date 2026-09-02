from pathlib import Path
from datetime import datetime
import py_compile, shutil, sys

TARGET = Path("alliance_property_brain_foundation_v1.py")
if not TARGET.exists():
    raise SystemExit("ERROR: alliance_property_brain_foundation_v1.py not found. Run from repo root.")

src = TARGET.read_text(encoding="utf-8")

if 'VERSION = "1.9.24-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9.24 baseline.")
if "# FOUNDATION_1_9W_SHARED_TAIL_CONTACT_RECOVERY" in src:
    print("FOUNDATION_1_9W_ALREADY_INSTALLED")
    sys.exit(0)

anchor = "def _v18_shared_source_contacts(source_raw: str) -> List[Dict[str, Any]]:\n"
end_anchor = "\ndef _v18_merge_source_contacts(\n"
start = src.find(anchor)
end = src.find(end_anchor, start)
if start < 0 or end < 0:
    raise SystemExit("ERROR: v18 shared-contact function anchors not found.")

replacement = r'''# FOUNDATION_1_9W_SHARED_TAIL_CONTACT_RECOVERY
def _v18_shared_source_contacts(source_raw: str) -> List[Dict[str, Any]]:
    # Recover shared broker/source contacts from the ORIGINAL source message.
    # 1.9W safely supports footer blocks without CONTACT/CALL keywords.
    raw = str(source_raw or "")
    if not raw.strip():
        return []

    line_ranges = _line_ranges(raw)
    if not line_ranges:
        return []

    footer_start = None
    for start, _end, line in line_ranges:
        if V18_FOOTER_SIGNAL_RE.search(_boundary_clean_line(line)):
            footer_start = start
            break

    if footer_start is None:
        last_property_fact_end = 0
        phone_line_indexes = []

        for idx, (ls, le, line) in enumerate(line_ranges):
            clean = _boundary_clean_line(line)
            if (
                PROPERTY_FACT_RE.search(clean)
                or AREA_RE.search(clean)
                or MONEY_RE.search(clean)
                or re.search(r"\b\d+(?:\.\d+)?\s*BHK\b", clean, re.I)
                or re.search(r"@\s*\d", clean)
            ):
                last_property_fact_end = max(last_property_fact_end, le)

            if V18_FOOTER_PHONE_RE.search(str(line or "")):
                phone_line_indexes.append(idx)

        trailing_phone_indexes = [
            idx for idx in phone_line_indexes
            if line_ranges[idx][0] >= last_property_fact_end
        ]

        if trailing_phone_indexes:
            first_phone_idx = trailing_phone_indexes[0]
            candidate_idx = first_phone_idx
            steps = 0
            j = first_phone_idx - 1

            while j >= 0 and steps < 2:
                ls, le, line = line_ranges[j]
                clean = _boundary_clean_line(line)
                if not clean:
                    j -= 1
                    continue
                if le <= last_property_fact_end:
                    break
                if (
                    PROPERTY_FACT_RE.search(clean)
                    or AREA_RE.search(clean)
                    or MONEY_RE.search(clean)
                    or re.search(r"\b\d+(?:\.\d+)?\s*BHK\b", clean, re.I)
                ):
                    break
                candidate_idx = j
                steps += 1
                j -= 1

            footer_start = line_ranges[candidate_idx][0]

    if footer_start is None:
        return []

    footer = raw[footer_start:].strip()
    phone_matches = list(V18_FOOTER_PHONE_RE.finditer(footer))
    if not phone_matches:
        return []

    footer_lines = [
        re.sub(r"[*_`]+", "", re.sub(r"\s+", " ", x)).strip()
        for x in footer.splitlines()
    ]
    footer_lines = [x for x in footer_lines if x]

    result: List[Dict[str, Any]] = []
    seen = set()

    for match in phone_matches:
        phone = _v18_normalize_phone(match.group(0))
        if not phone or phone in seen:
            continue
        seen.add(phone)

        phone_digits = re.sub(r"\D", "", match.group(0))
        phone_line_index = None
        for idx, line in enumerate(footer_lines):
            if phone_digits and phone_digits in re.sub(r"\D", "", line):
                phone_line_index = idx
                break

        name = None
        company = None

        if phone_line_index is not None:
            before = []
            for line in footer_lines[max(0, phone_line_index - 3):phone_line_index]:
                if V18_FOOTER_SIGNAL_RE.search(line):
                    continue
                if V18_FOOTER_PHONE_RE.search(line):
                    continue
                if PROPERTY_FACT_RE.search(line) or AREA_RE.search(line) or MONEY_RE.search(line):
                    continue
                before.append(line)

            if before:
                name = before[-1]

            for line in footer_lines[phone_line_index + 1:]:
                if V18_FOOTER_PHONE_RE.search(line):
                    continue
                if V18_FOOTER_SIGNAL_RE.search(line):
                    continue
                if PROPERTY_FACT_RE.search(line) or AREA_RE.search(line) or MONEY_RE.search(line):
                    continue
                if line:
                    company = line
                    break

        result.append({
            "phone": phone,
            "name": name,
            "company": company,
            "role": "SOURCE_CONTACT",
            "provenance": "SOURCE_SHARED_CONTACT",
            "scope": "SHARED_SOURCE_MESSAGE",
            "owner_status": "NOT_PROVEN",
            "broker_status": "SOURCE_OR_BROKER_CONTEXT",
            "evidence": footer,
        })

    return result
'''

src = src[:start] + replacement + src[end:]

src = src.replace(
    'VERSION = "1.9.24-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    'VERSION = "1.9.25-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    'MODE = "PROJECT_BHK_ATOMIC_SPLIT_1_9V"',
    'MODE = "SHARED_TAIL_CONTACT_RECOVERY_1_9W"',
    1,
)
src = src.replace(
    'p["shared_source_contact_provenance"] = "MESSAGE_FOOTER"',
    'p["shared_source_contact_provenance"] = "SOURCE_SHARED_CONTACT"',
    1,
)

backup = TARGET.with_name(
    TARGET.name + ".before-1_9W-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
)
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)

    ns = {}
    exec(compile(src, str(TARGET), "exec"), ns)
    sample = """*VIPUL BELMONTE - SALE -*SIZE - 2450SQFT*
*3BHK+SQ FULLY RENOVATED SUN/PARK FACING*

*THE SUMMIT 4BHK(3034) OPP. DLF SUMMIT PLAZA*

*WITH 4 PARKING NICELY DONE UP APARTMENT WITH AC @9.25CR.*

*THE SUMMIT 4BHK(3400) CORNER POOL/ARAVALI/SUN FACING WITH 3 PARKING @12.10CR.*

*DLF PARK PLACE 4BHK ARAVALI/SUN FACING RENTED APARTMENT @9.60CR*

*NARESH SHARMA*
*98180 48111*
*98180 88111*

*REAL P🎯INT*"""
    got = ns["_v18_shared_source_contacts"](sample)
    phones = sorted(x.get("phone") for x in got)
    expected = sorted(["+919818048111", "+919818088111"])
    if phones != expected:
        raise RuntimeError(f"1.9W regression failed: expected {expected}, got {phones}")
    if not all(x.get("provenance") == "SOURCE_SHARED_CONTACT" for x in got):
        raise RuntimeError("1.9W provenance regression failed")
except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9W_INSTALL_PASS")
print("Recovered shared contacts: +919818048111, +919818088111")
print("Provenance: SOURCE_SHARED_CONTACT")
print("Version: 1.9.25-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: SHARED_TAIL_CONTACT_RECOVERY_1_9W")
print("Backup:", backup)
