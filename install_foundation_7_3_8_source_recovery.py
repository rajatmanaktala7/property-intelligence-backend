from __future__ import annotations

from pathlib import Path
from datetime import datetime
import py_compile
import shutil

VERSION = "7.3.8-ALLIANCE-SOURCE-RECOVERY-REEXTRACTION"

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "alliance_source_recovery_v738.py"
ENTRY = ROOT / "production_entrypoint.py"
WORKSPACE = ROOT / "alliance_primary_workspace_v730.py"

for p in (ENGINE, ENTRY, WORKSPACE):
    if not p.exists():
        raise SystemExit(f"Required file missing: {p.name}")

# Verify the new engine before touching production files.
import alliance_source_recovery_v738 as recovery
result = recovery.self_test()
if result.get("status") != "PASS":
    raise SystemExit("7.3.8 self-test failed")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
entry_backup = ROOT / f"production_entrypoint-before-v738-{stamp}.py"
workspace_backup = ROOT / f"alliance_primary_workspace_v730-before-v738-{stamp}.py"
shutil.copy2(ENTRY, entry_backup)
shutil.copy2(WORKSPACE, workspace_backup)

entry = ENTRY.read_text(encoding="utf-8-sig")

# Register against wrapped.core. This deliberately avoids the 7.3.7 wrapper/core bug.
anchor = """        import alliance_live_feed_purity as live_feed_purity
        live_feed_purity.register(wrapped)
"""
block = """        # 7.3.8 historical source recovery is audit-only and fail-safe.
        try:
            import alliance_source_recovery_v738 as source_recovery_v738
            recovery_result = source_recovery_v738.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["source_recovery_v738"] = recovery_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["source_recovery_v738"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[source-recovery-v738] warning:", type(exc).__name__, str(exc))

"""
if "source_recovery_v738.register(wrapped.core)" not in entry:
    if anchor not in entry:
        raise SystemExit("Production entrypoint anchor not found. No files changed.")
    entry = entry.replace(anchor, block + anchor, 1)

ENTRY.write_text(entry, encoding="utf-8")

workspace = WORKSPACE.read_text(encoding="utf-8-sig")
workspace = workspace.replace(
    "7.3.7-ALLIANCE-HISTORICAL-EVIDENCE-REPAIR",
    "7.3.8-ALLIANCE-SOURCE-RECOVERY-REEXTRACTION",
)
workspace = workspace.replace(
    "Alliance CRE Operating System · 7.3.7",
    "Alliance CRE Operating System · 7.3.8",
)

# Add Source Recovery nav next to Data Repair if PRIMARY_NAV is still structured as expected.
if '("Source Recovery","/alliance/primary/source-recovery")' not in workspace:
    marker = '("Data Repair","/alliance/primary/data-repair")'
    if marker in workspace:
        workspace = workspace.replace(
            marker,
            marker + ',\n    ("Source Recovery","/alliance/primary/source-recovery")',
            1,
        )
    else:
        marker2 = '("Data Repair", "/alliance/primary/data-repair")'
        if marker2 in workspace:
            workspace = workspace.replace(
                marker2,
                marker2 + ',\n    ("Source Recovery", "/alliance/primary/source-recovery")',
                1,
            )
        else:
            raise SystemExit("Workspace Data Repair navigation anchor not found. No workspace write performed.")

WORKSPACE.write_text(workspace, encoding="utf-8")

for p in (ENGINE, ENTRY, WORKSPACE):
    py_compile.compile(str(p), doraise=True)

print(VERSION)
print("SELF-TEST: PASS")
print("MODE: AUDIT FIRST")
print("ROUTE: /alliance/primary/source-recovery")
print("API: /api/alliance/v738/status")
print("SOURCES: MAGAZINE + NEWSPAPER")
print("RECOVERY: source_table/source_pk -> historical row -> raw text / section / image-file reference")
print("VISION: image references are inventoried only in this safety stage; no blind external reprocessing")
print("MASTER DATABASE: NOT MUTATED")
print("SOURCE ROWS: NOT MUTATED")
print("CANONICAL IDS: PRESERVED")
print("Dockerfile: NOT MODIFIED")
print("Entrypoint backup:", entry_backup)
print("Workspace backup:", workspace_backup)
