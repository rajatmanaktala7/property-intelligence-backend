from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT=Path(__file__).resolve().parent
NEW=ROOT/"alliance_organized_main_v933.py"
TARGET=ROOT/"alliance_organized_main_v930.py"

if not NEW.exists():
    raise RuntimeError("alliance_organized_main_v933.py not found")

stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
if TARGET.exists():
    shutil.copy2(TARGET, TARGET.with_name("alliance_organized_main_v930.before-v933-"+stamp+".py"))

shutil.copy2(NEW,TARGET)
py_compile.compile(str(TARGET),doraise=True)
print("Alliance 9.3.3 fast-stats fix installed.")
print("Main dashboard no longer scans up to 1,500 rows across all 10 databases.")
print("No database records, schemas, matcher logic, or source evidence changed.")
