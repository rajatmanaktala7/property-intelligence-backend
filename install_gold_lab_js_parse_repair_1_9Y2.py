from pathlib import Path
from datetime import datetime
import py_compile
import shutil
import sys
import re

TARGET = Path("alliance_property_brain_foundation_v1.py")

if not TARGET.exists():
    raise SystemExit(
        "ERROR: alliance_property_brain_foundation_v1.py not found. "
        "Run this installer from the repository root."
    )

src = TARGET.read_text(encoding="utf-8")

if 'VERSION = "1.9.27-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9.27 baseline.")
if 'MODE = "SPLIT_SOURCE_LABELING_FLOW_1_9Y"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9Y mode.")

BAD = "\n# FOUNDATION_1_9Y_STAY_ON_SPLIT_SOURCE\nasync function loadNextInSourceOrGlobal(sourceId){"
GOOD = "\n// FOUNDATION_1_9Y_STAY_ON_SPLIT_SOURCE\nasync function loadNextInSourceOrGlobal(sourceId){"

if BAD not in src:
    if GOOD in src:
        print("FOUNDATION_1_9Y2_ALREADY_INSTALLED")
        sys.exit(0)
    raise SystemExit(
        "ERROR: exact 1.9Y JavaScript marker not found. "
        "Current file was not modified."
    )

# Root-cause repair: '#' is a Python comment, but inside <script> it is invalid
# JavaScript and stops the entire Gold Lab script from parsing.
src = src.replace(BAD, GOOD, 1)

src = src.replace(
    'VERSION = "1.9.27-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    'VERSION = "1.9.28-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    'MODE = "SPLIT_SOURCE_LABELING_FLOW_1_9Y"',
    'MODE = "GOLD_LAB_JS_PARSE_REPAIR_1_9Y2"',
    1,
)

backup = TARGET.with_name(
    TARGET.name + ".before-1_9Y2-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
)
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)

    # Extract Gold Lab script and apply deterministic static checks.
    lab_start = src.find('LAB_UI = r"""')
    if lab_start < 0:
        raise RuntimeError("LAB_UI not found.")
    lab_end = src.find('DASHBOARD_UI = r"""', lab_start)
    if lab_end < 0:
        raise RuntimeError("DASHBOARD_UI boundary not found.")
    lab = src[lab_start:lab_end]

    script_start = lab.find("<script>")
    script_end = lab.find("</script>", script_start)
    if script_start < 0 or script_end < 0:
        raise RuntimeError("Gold Lab <script> block not found.")
    js = lab[script_start + len("<script>"):script_end]

    checks = {
        "bad_hash_comment_removed":
            "# FOUNDATION_1_9Y_STAY_ON_SPLIT_SOURCE" not in js,
        "valid_js_comment_present":
            "// FOUNDATION_1_9Y_STAY_ON_SPLIT_SOURCE" in js,
        "helper_present":
            "async function loadNextInSourceOrGlobal(sourceId)" in js,
        "confirm_same_source":
            "await loadNextInSourceOrGlobal(splitSourceId);" in js,
        "save_same_source":
            "await loadNextInSourceOrGlobal(savedSourceId);" in js,
        "initial_load_preserved":
            "refreshProgress();" in js and "loadNext();" in js,
        "splitter_1_9x_preserved":
            "# FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT" in src,
        "shared_contacts_1_9w_preserved":
            "# FOUNDATION_1_9W_SHARED_TAIL_CONTACT_RECOVERY" in src,
        "version":
            'VERSION = "1.9.28-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' in src,
        "mode":
            'MODE = "GOLD_LAB_JS_PARSE_REPAIR_1_9Y2"' in src,
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("1.9Y2 validation failed: " + ", ".join(failed))

    # Catch any other Python-style full-line comments accidentally inserted
    # into the Gold Lab JavaScript.
    bad_js_lines = [
        line.strip()
        for line in js.splitlines()
        if line.lstrip().startswith("#")
    ]
    if bad_js_lines:
        raise RuntimeError(
            "Gold Lab JavaScript still contains invalid # comment(s): "
            + " | ".join(bad_js_lines[:5])
        )

except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9Y2_INSTALL_PASS")
print("Root cause fixed: invalid # comment inside Gold Lab JavaScript")
print("Gold Lab initial loading: restored")
print("Confirm Split same-source flow: preserved")
print("Save next-child same-source flow: preserved")
print("1.9X atomic splitter: preserved")
print("1.9W shared contacts: preserved")
print("Version: 1.9.28-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: GOLD_LAB_JS_PARSE_REPAIR_1_9Y2")
print("Backup:", backup)
