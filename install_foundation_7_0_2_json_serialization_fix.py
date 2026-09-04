from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_unified_data_repair_v700.py"
APP=ROOT/"app.py"

EXPECTED_OLD='VERSION="7.0.1-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR-COLLECTIONS-FIX"'
EXPECTED_NEW='VERSION="7.0.2-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR-JSON-SERIALIZATION-FIX"'

OLD_IMPORT='from datetime import datetime, timezone\n'
NEW_IMPORT='from datetime import date, datetime, time as dt_time, timezone\nimport uuid\n'

OLD_SAFE='def _safe(v):\n    if isinstance(v,Decimal): return float(v)\n    if isinstance(v,(datetime,)): return v.isoformat()\n    return v\ndef _jsonable(row):return {str(k):_safe(v) for k,v in row.items()}\n'

NEW_SAFE='def _safe(v):\n    # Convert all common PostgreSQL/Python values into JSON-safe values.\n    # This is deliberately recursive because source rows can contain JSON/ARRAY values.\n    if v is None or isinstance(v,(str,int,float,bool)):\n        return v\n    if isinstance(v,Decimal):\n        return float(v)\n    if isinstance(v,(datetime,date,dt_time)):\n        return v.isoformat()\n    if isinstance(v,uuid.UUID):\n        return str(v)\n    if isinstance(v,(bytes,bytearray,memoryview)):\n        return bytes(v).hex()\n    if isinstance(v,dict):\n        return {str(k):_safe(val) for k,val in v.items()}\n    if isinstance(v,(list,tuple,set)):\n        return [_safe(x) for x in v]\n    # Last-resort safety for uncommon DB scalar types.\n    return str(v)\ndef _jsonable(row):return {str(k):_safe(v) for k,v in row.items()}\n'

def chk(src,name):
    compile(src,name,"exec")

def main():
    if not MOD.exists():
        raise SystemExit("alliance_unified_data_repair_v700.py not found")
    if not APP.exists():
        raise SystemExit("app.py not found")

    src=MOD.read_text(encoding="utf-8")

    if EXPECTED_NEW in src:
        print("7.0.2 already installed.")
        return
    if EXPECTED_OLD not in src:
        raise SystemExit("Expected 7.0.1 version not found. Refusing unsafe overwrite.")
    if OLD_SAFE not in src:
        raise SystemExit("Expected _safe() block not found. Refusing unsafe overwrite.")
    if OLD_IMPORT not in src:
        raise SystemExit("Expected datetime import not found. Refusing unsafe overwrite.")

    backup=ROOT/f"alliance_unified_data_repair_v700-before-702-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)

    src=src.replace(EXPECTED_OLD,EXPECTED_NEW,1)
    src=src.replace(OLD_IMPORT,NEW_IMPORT,1)
    src=src.replace(OLD_SAFE,NEW_SAFE,1)

    chk(src,str(MOD))

    # Runtime serialization self-test.
    ns={}
    exec(compile(src,str(MOD),"exec"),ns)
    import json, uuid
    from datetime import date, datetime, time
    from decimal import Decimal
    sample={
        "d":date(2026,9,4),
        "dt":datetime(2026,9,4,10,0,0),
        "t":time(10,1,2),
        "money":Decimal("12.50"),
        "uuid":uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "nested":[{"d":date(2026,9,5)}]
    }
    encoded=json.dumps(ns["_safe"](sample))
    if "2026-09-04" not in encoded or "12.5" not in encoded:
        raise SystemExit("JSON serialization self-test failed")

    MOD.write_text(src,encoding="utf-8")

    app=APP.read_text(encoding="utf-8")
    if "FOUNDATION_7_0_UNIFIED_NEWSPAPER_MAGAZINE_DATA_REPAIR" not in app:
        raise SystemExit("7.0 app registration marker missing")
    chk(app,str(APP))

    print("FOUNDATION 7.0.2 INSTALLED")
    print("Root fault fixed: PostgreSQL DATE values were not JSON serializable.")
    print("Also hardened serialization for datetime/time/Decimal/UUID/bytes/nested arrays and JSON.")
    print("No cleanup logic, dedupe logic, source data, Gold, Champion, or production tables changed.")
    print("The existing partial 5,878-row derived shadow will be safely rebuilt on restart.")
    print("Dashboard remains: /property-brain/unified-data-repair-v700")
    print("Backup:",backup)

if __name__=="__main__":
    main()
