from pathlib import Path
from datetime import datetime
import re, shutil

ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_unified_data_repair_v700.py"
APP=ROOT/"app.py"

NEW_VERSION='VERSION="7.0.3-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR-SERIALIZATION-CLOSURE"'

NEW_SAFE = '''def _safe(v):
    # Recursive JSON-safe conversion for PostgreSQL/Python source values.
    if v is None or isinstance(v,(str,int,float,bool)):
        return v
    if isinstance(v,Decimal):
        return float(v)
    if isinstance(v,(datetime,date,dt_time)):
        return v.isoformat()
    if isinstance(v,uuid.UUID):
        return str(v)
    if isinstance(v,(bytes,bytearray,memoryview)):
        return bytes(v).hex()
    if isinstance(v,dict):
        return {str(k):_safe(val) for k,val in v.items()}
    if isinstance(v,(list,tuple,set)):
        return [_safe(x) for x in v]
    return str(v)
def _jsonable(row):
    return {str(k):_safe(v) for k,v in row.items()}
'''

def compile_ok(src,name):
    compile(src,name,"exec")

def main():
    if not MOD.exists():
        raise SystemExit("alliance_unified_data_repair_v700.py not found")
    if not APP.exists():
        raise SystemExit("app.py not found")

    src=MOD.read_text(encoding="utf-8")
    if "ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR" not in src:
        raise SystemExit("Unexpected module. Refusing unsafe modification.")

    backup=ROOT/f"alliance_unified_data_repair_v700-before-703-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)

    src=re.sub(
        r'from datetime import [^\n]+\n',
        'from datetime import date, datetime, time as dt_time, timezone\n',
        src,
        count=1
    )
    if not re.search(r'^import uuid$',src,re.M):
        anchor='from decimal import Decimal\n'
        if anchor not in src:
            raise SystemExit("Decimal import anchor missing")
        src=src.replace(anchor,anchor+'import uuid\n',1)

    pat=r'def _safe\(v\):\n.*?def _jsonable\(row\):[^\n]*\n'
    new_src,n=re.subn(pat,NEW_SAFE,src,count=1,flags=re.S)
    if n!=1:
        raise SystemExit("Could not safely replace _safe/_jsonable block")
    src=new_src

    src=re.sub(
        r'VERSION="7\.0\.[^"]+"',
        NEW_VERSION,
        src,
        count=1
    )

    compile_ok(src,str(MOD))

    test_ns={}
    test_code='''from datetime import date, datetime, time as dt_time
from decimal import Decimal
import uuid
'''+NEW_SAFE+'''
sample={
 "d":date(2026,9,4),
 "dt":datetime(2026,9,4,10,0,0),
 "t":dt_time(10,1,2),
 "money":Decimal("12.50"),
 "uuid":uuid.UUID("12345678-1234-5678-1234-567812345678"),
 "nested":{"rows":[{"d":date(2026,9,5),"x":Decimal("1.25")}]}
}
result=_safe(sample)
'''
    exec(compile(test_code,"v703-serialization-selftest","exec"),test_ns)
    import json
    payload=json.dumps(test_ns["result"])
    assert "2026-09-04" in payload
    assert "12.5" in payload
    assert "2026-09-05" in payload

    MOD.write_text(src,encoding="utf-8")

    verify=MOD.read_text(encoding="utf-8")
    if NEW_VERSION not in verify:
        raise SystemExit("Version verification failed")
    if "from datetime import date, datetime, time as dt_time, timezone" not in verify:
        raise SystemExit("date import verification failed")
    if "isinstance(v,(datetime,date,dt_time))" not in verify:
        raise SystemExit("recursive serializer verification failed")

    compile_ok(APP.read_text(encoding="utf-8"),str(APP))
    compile_ok(verify,str(MOD))

    print("FOUNDATION 7.0.3 INSTALLED")
    print("ROOT CAUSE CLOSED: database DATE values are now recursively JSON-safe.")
    print("Also covers datetime, time, Decimal, UUID, bytes and nested dict/list values.")
    print("No source records, Gold, Champion, cleanup semantics or dedupe semantics changed.")
    print("IMPORTANT: verify GitHub contains version 7.0.3 after push.")
    print("Dashboard remains /property-brain/unified-data-repair-v700")
    print("Backup:",backup)

if __name__=="__main__":
    main()
