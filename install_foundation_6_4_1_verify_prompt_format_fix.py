from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
MOD=ROOT/'alliance_magazine_occurrence_v640.py'
APP=ROOT/'app.py'

def chk(s,n):
    compile(s,n,'exec')

def main():
    if not MOD.exists():
        raise SystemExit('alliance_magazine_occurrence_v640.py not found')

    src=MOD.read_text(encoding='utf-8')
    if 'VERSION="6.4.0-ALLIANCE-MAGAZINE-OCCURRENCE-AWARE-FULL-PAGE-BATCH-REPAIR"' not in src and 'VERSION="6.4.1-ALLIANCE-MAGAZINE-OCCURRENCE-AWARE-FULL-PAGE-BATCH-REPAIR-FORMAT-FIX"' not in src:
        raise SystemExit('Unexpected 6.4 module version. Refusing to overwrite.')

    backup=ROOT/f"alliance_magazine_occurrence_v640-before-641-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)

    src=src.replace(
        'VERSION="6.4.0-ALLIANCE-MAGAZINE-OCCURRENCE-AWARE-FULL-PAGE-BATCH-REPAIR"',
        'VERSION="6.4.1-ALLIANCE-MAGAZINE-OCCURRENCE-AWARE-FULL-PAGE-BATCH-REPAIR-FORMAT-FIX"'
    )
    src=src.replace(
        'MODE="LOCK_199_FROM_631_TRANSCRIBE_ALL_ROWS_IN_ORDER_MATCH_BY_REF_OCCURRENCE_PARSE_ONLY_11_FAILURES_NO_FRESH_EXAM"',
        'MODE="SAME_640_OCCURRENCE_LOGIC_ESCAPE_VERIFY_JSON_BRACES_NO_SEMANTIC_CHANGE_NO_FRESH_EXAM"'
    )

    old='Return JSON exactly:\n{"found":true|false,"ref":"","occurrence":0,"raw_line":""}\n'
    new='Return JSON exactly:\n{{"found":true|false,"ref":"","occurrence":0,"raw_line":""}}\n'
    if old not in src and new not in src:
        raise SystemExit('Expected VERIFY_PROMPT JSON block not found')
    src=src.replace(old,new)

    chk(src,str(MOD))

    marker='VERIFY_PROMPT="""'
    start=src.index(marker)+len(marker)
    end=src.index('"""',start)
    prompt=src[start:end]
    rendered=prompt.format(ref='G-97',occurrence=2)
    if '"found":true|false' not in rendered or 'REFERENCE: G-97' not in rendered or 'OCCURRENCE: 2' not in rendered:
        raise SystemExit('VERIFY_PROMPT formatting self-test failed')

    MOD.write_text(src,encoding='utf-8')

    app=APP.read_text(encoding='utf-8')
    if 'FOUNDATION_6_4_MAGAZINE_OCCURRENCE_AWARE_FULL_PAGE_BATCH_REPAIR' not in app:
        raise SystemExit('6.4 app registration marker missing')
    chk(app,str(APP))

    print('FOUNDATION 6.4.1 INSTALLED')
    print('Exact bug fixed: VERIFY_PROMPT used Python .format() while JSON braces were unescaped.')
    print('Literal JSON braces are now escaped before .format().')
    print('No occurrence logic, truth, exam, Gold, Champion, canonical data or 199/210 parent changed.')
    print('Dashboard remains: /property-brain/magazine-occurrence-v640')
    print('Backup:',backup)

if __name__=='__main__':
    main()
