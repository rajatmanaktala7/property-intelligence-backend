from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "alliance_historical_repair_v737.py"
ENTRY = ROOT / "production_entrypoint.py"
WORKSPACE = ROOT / "alliance_primary_workspace_v730.py"

TARGET = "7.3.7-ALLIANCE-HISTORICAL-EVIDENCE-REPAIR"
MARKER = "# 7.3.7 HISTORICAL EVIDENCE REPAIR REGISTRATION"

for p in [ENGINE, ENTRY, WORKSPACE]:
    if not p.exists():
        raise SystemExit(f"ERROR: missing {p.name}")

# Self-test BEFORE any source modification.
cp = subprocess.run([sys.executable, str(ENGINE)], cwd=str(ROOT), capture_output=True, text=True)
if cp.returncode != 0 or '"status": "PASS"' not in cp.stdout:
    print(cp.stdout)
    print(cp.stderr)
    raise SystemExit("ERROR: 7.3.7 repair-engine self-test failed")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

entry = ENTRY.read_text(encoding="utf-8")
entry_backup = None
if MARKER not in entry:
    anchor = """        stabilization = production_surface.register(wrapped)

        import alliance_live_feed_purity as live_feed_purity"""
    replacement = """        stabilization = production_surface.register(wrapped)

        # 7.3.7 Historical evidence repair is fail-safe and dry-run by default.
        try:
            import alliance_historical_repair_v737 as historical_repair_v737
            repair_result = historical_repair_v737.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["historical_repair_v737"] = repair_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["historical_repair_v737"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[historical-repair-v737] warning:", type(exc).__name__, str(exc))

        import alliance_live_feed_purity as live_feed_purity"""
    if anchor not in entry:
        raise SystemExit("ERROR: production_entrypoint registration anchor not found")
    entry_backup = ROOT / f"production_entrypoint-before-v737-{stamp}.py"
    shutil.copy2(ENTRY, entry_backup)
    entry = entry.replace(anchor, replacement, 1)
    entry += "\n\n" + MARKER + "\n"
    compile(entry, str(ENTRY), "exec")
    ENTRY.write_text(entry, encoding="utf-8")

workspace = WORKSPACE.read_text(encoding="utf-8")
workspace_backup = None
if TARGET not in workspace:
    if 'VERSION="7.3.6-ALLIANCE-MAGAZINE-SECTION-CONTEXT"' not in workspace:
        raise SystemExit("ERROR: 7.3.6 workspace foundation not found")

    workspace_backup = ROOT / f"alliance_primary_workspace_v730-before-v737-{stamp}.py"
    shutil.copy2(WORKSPACE, workspace_backup)

    workspace = workspace.replace(
        'VERSION="7.3.6-ALLIANCE-MAGAZINE-SECTION-CONTEXT"',
        'VERSION="7.3.7-ALLIANCE-HISTORICAL-EVIDENCE-REPAIR"',
        1,
    )
    workspace = workspace.replace(
        '("Follow-ups","/alliance/primary/followups"),\n("Add Property","/property-manual"),',
        '("Follow-ups","/alliance/primary/followups"),\n("Data Repair","/alliance/primary/data-repair"),\n("Add Property","/property-manual"),',
        1,
    )
    workspace = workspace.replace(
        "Alliance CRE Operating System · 7.3.6",
        "Alliance CRE Operating System · 7.3.7",
        1,
    )
    compile(workspace, str(WORKSPACE), "exec")
    WORKSPACE.write_text(workspace, encoding="utf-8")

# Compile every modified/current dependency.
for p in [ENGINE, ENTRY, WORKSPACE]:
    subprocess.run([sys.executable, "-m", "py_compile", str(p)], check=True, cwd=str(ROOT))

print(TARGET)
print("SELF-TEST: PASS")
print("MODE: DRY RUN FIRST")
print("ROUTE: /alliance/primary/data-repair")
print("SOURCES: MAGAZINE + NEWSPAPER")
print("SAFETY: canonical IDs preserved; no new master records; no source rows mutated")
print("APPLY: only VERIFIED_REPAIR candidates; admin only")
print("SOURCE MISSING: flagged, never fabricated")
print("DUPLICATES: no property insertion; multiple source links remain evidence on the same canonical property")
print("Dockerfile: NOT MODIFIED")
if entry_backup:
    print("Entrypoint backup:", entry_backup)
if workspace_backup:
    print("Workspace backup:", workspace_backup)
