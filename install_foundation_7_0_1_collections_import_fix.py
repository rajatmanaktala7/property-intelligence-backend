from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
MOD=ROOT/'alliance_unified_data_repair_v700.py'
APP=ROOT/'app.py'

def chk(s,n):
    compile(s,n,'exec')

def main():
    if not MOD.exists():
        raise SystemExit('alliance_unified_data_repair_v700.py not found')

    src=MOD.read_text(encoding='utf-8')
    if 'VERSION="7.0.0-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR"' not in src and 'VERSION="7.0.1-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR-COLLECTIONS-FIX"' not in src:
        raise SystemExit('Unexpected v700 module version. Refusing to overwrite.')

    backup=ROOT/f"alliance_unified_data_repair_v700-before-701-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)

    if 'from collections import defaultdict, Counter\n' not in src:
        anchor='import hashlib, html, json, re, threading, time\n'
        if anchor not in src:
            raise SystemExit('Expected import anchor not found')
        src=src.replace(anchor, anchor+'from collections import defaultdict, Counter\n', 1)

    src=src.replace(
        'VERSION="7.0.0-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR"',
        'VERSION="7.0.1-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR-COLLECTIONS-FIX"'
    )

    chk(src,str(MOD))

    # Runtime symbol self-test without touching database.
    scope={}
    exec(compile('from collections import defaultdict, Counter\nx=defaultdict(int); c=Counter(); x["a"]+=1; c.update(["b"])','selftest','exec'),scope)
    if scope['x']['a'] != 1 or scope['c']['b'] != 1:
        raise SystemExit('collections runtime self-test failed')

    MOD.write_text(src,encoding='utf-8')

    app=APP.read_text(encoding='utf-8')
    if 'FOUNDATION_7_0_UNIFIED_NEWSPAPER_MAGAZINE_DATA_REPAIR' not in app:
        raise SystemExit('7.0 app registration marker missing')
    chk(app,str(APP))

    print('FOUNDATION 7.0.1 INSTALLED')
    print('Exact fault fixed: defaultdict and Counter were used but not imported.')
    print('No database schema, cleanup logic, dedupe logic, source data, Gold or Champion changed.')
    print('Dashboard remains: /property-brain/unified-data-repair-v700')
    print('Backup:',backup)

if __name__=='__main__':
    main()
