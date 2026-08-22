from pathlib import Path
import shutil
from datetime import datetime

APP = Path("app.py")
MARKER = "# === WHATSAPP INTELLIGENCE MODULE ==="
BLOCK = """
# === WHATSAPP INTELLIGENCE MODULE ===
from whatsapp_intelligence import router as whatsapp_intelligence_router
app.include_router(whatsapp_intelligence_router)
# === END WHATSAPP INTELLIGENCE MODULE ===
"""

if not APP.exists():
    raise SystemExit("ERROR: app.py not found. Run this from the repository root.")

text = APP.read_text(encoding="utf-8")
if MARKER in text:
    print("WhatsApp Intelligence is already installed in app.py.")
    raise SystemExit(0)

# Insert immediately before the first top-level `if __name__ == "__main__"` if present.
needle = '\nif __name__ == "__main__"'
pos = text.find(needle)
if pos < 0:
    # Safe fallback: append at end.
    pos = len(text)

backup = Path(f"app-before-whatsapp-intelligence-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py")
shutil.copy2(APP, backup)
new_text = text[:pos] + "\n" + BLOCK + "\n" + text[pos:]
APP.write_text(new_text, encoding="utf-8")

print("Installed WhatsApp Intelligence router.")
print("Backup:", backup)
print("Next run:")
print("  python -m py_compile app.py whatsapp_intelligence.py")
print("  git diff --check")
print("  git status --short")
