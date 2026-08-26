from pathlib import Path
import shutil
from datetime import datetime

APP = Path("app.py")
MARKER = "# === WHATSAPP LIVE BRIDGE MODULE ==="
BLOCK = """
# === WHATSAPP LIVE BRIDGE MODULE ===
from whatsapp_live_bridge import router as whatsapp_live_bridge_router
app.include_router(whatsapp_live_bridge_router)
# === END WHATSAPP LIVE BRIDGE MODULE ===
"""

if not APP.exists():
    raise SystemExit(
        "ERROR: app.py not found.\n"
        "Open PowerShell in C:\\Users\\jasleen\\Desktop\\property-intelligence-backend "
        "and run this installer again."
    )

text = APP.read_text(encoding="utf-8")

if MARKER in text:
    print("WhatsApp Live Bridge is already registered in app.py.")
    print("No app.py changes were required.")
    raise SystemExit(0)

if not Path("whatsapp_live_bridge.py").exists():
    raise SystemExit(
        "ERROR: whatsapp_live_bridge.py is missing from this folder. "
        "Do not continue until that file exists."
    )

needle = '\nif __name__ == "__main__"'
pos = text.find(needle)
if pos < 0:
    pos = len(text)

backup = Path(
    f"app-before-whatsapp-live-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
)
shutil.copy2(APP, backup)

new_text = text[:pos] + "\n" + BLOCK + "\n" + text[pos:]
APP.write_text(new_text, encoding="utf-8")

print("SUCCESS: WhatsApp Live Bridge registered.")
print("Backup:", backup)
print("")
print("Run next:")
print("  python -m py_compile app.py whatsapp_live_bridge.py")
print("  git diff --check")
print("  git status --short")
