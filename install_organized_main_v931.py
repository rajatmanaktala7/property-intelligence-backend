from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT = Path(__file__).resolve().parent
NEW = ROOT / "alliance_organized_main_v931.py"
TARGET = ROOT / "alliance_organized_main_v930.py"

if not NEW.exists():
    raise RuntimeError("alliance_organized_main_v931.py not found")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
if TARGET.exists():
    shutil.copy2(TARGET, TARGET.with_name("alliance_organized_main_v930.before-v931-" + stamp + ".py"))

shutil.copy2(NEW, TARGET)

py_compile.compile(str(TARGET), doraise=True)
print("Alliance 9.3.1 route/dashboard fix installed.")
print("Fixed _property_rows argument error on /alliance/primary.")
print("No database records or schema were changed.")
