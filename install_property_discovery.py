from pathlib import Path
import shutil
import sys

MARKER = "# === PROPERTY DISCOVERY V17 INTEGRATION ==="

def main():
    root = Path.cwd()
    app_file = root / "app.py"
    module_file = root / "property_discovery.py"

    if not app_file.exists():
        print("ERROR: app.py not found. Run this from the property-intelligence-backend root folder.")
        sys.exit(1)
    if not module_file.exists():
        print("ERROR: property_discovery.py is not in this folder.")
        sys.exit(1)

    code = app_file.read_text(encoding="utf-8")
    required = ["app=FastAPI", "engine=create_engine", "def need_login", "def save_property", "def actor_name"]
    missing = [x for x in required if x not in code]
    if missing:
        print("ERROR: Safety check failed. Missing:", ", ".join(missing))
        print("No changes made.")
        sys.exit(1)

    if MARKER in code:
        print("Already installed. No changes made.")
        return

    backup = root / "app.py.before-property-discovery.bak"
    shutil.copy2(app_file, backup)

    # Add a visible navigation button inside the existing dashboard wherever
    # the standard Operations nav button is present. This is additive only.
    nav_anchor = '<a class="navbtn" href="/workspace">Operations</a>'
    nav_button = '<a class="navbtn" href="/property-discovery">Find Property</a>'
    if nav_button not in code and nav_anchor in code:
        code = code.replace(nav_anchor, nav_anchor + "\n" + nav_button)

    block = r'''

# === PROPERTY DISCOVERY V17 INTEGRATION ===
from property_discovery import install_property_discovery as _install_property_discovery
_install_property_discovery(
    app=app,
    engine=engine,
    need_login=need_login,
    save_property=save_property,
    actor_name=actor_name,
)
# === END PROPERTY DISCOVERY V17 INTEGRATION ===
'''
    app_file.write_text(code.rstrip() + block + "\n", encoding="utf-8")
    print("SUCCESS")
    print("Backup created:", backup.name)
    print("New route: /property-discovery")
    print("Existing routes and database tables were not removed.")

if __name__ == "__main__":
    main()
