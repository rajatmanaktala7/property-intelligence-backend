from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT=Path(__file__).resolve().parent
PE=ROOT/'production_entrypoint.py'
SRC=ROOT/'alliance_magazine_fastlane_v840.py'
if not SRC.exists():
    raise RuntimeError('alliance_magazine_fastlane_v840.py must be in the repository folder beside this installer')
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup=PE.with_name(PE.name+'.before-fastlane-8.4-'+stamp+'.bak')
shutil.copy2(PE,backup)
try:
    p=PE.read_text(encoding='utf-8')
    marker='        CORE_APP = wrapped.app'
    if marker not in p:raise RuntimeError('production_entrypoint insertion point not found')
    block='''        # ALLIANCE_MAGAZINE_FASTLANE_V840
        try:
            import alliance_magazine_fastlane_v840 as magazine_fastlane_v840
            fastlane_result = magazine_fastlane_v840.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_fastlane_v840"] = fastlane_result
            print("[magazine-fastlane-v840]", fastlane_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_fastlane_v840"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[magazine-fastlane-v840] warning:", type(exc).__name__, str(exc))

'''
    if 'ALLIANCE_MAGAZINE_FASTLANE_V840' not in p:
        p=p.replace(marker,block+marker,1)
    PE.write_text(p,encoding='utf-8')
    py_compile.compile(str(SRC),doraise=True)
    py_compile.compile(str(PE),doraise=True)
    print('CRE OS 8.4 FastLane installed successfully.')
    print('New route: /magazine-fastlane')
    print('External AI API calls: ZERO.')
    print('Stored September PDF is reused. No re-upload required.')
    print('Old 8.3.x AI Resume remains untouched and should stay OFF.')
except Exception:
    shutil.copy2(backup,PE)
    print('FAILED - production_entrypoint restored safely.')
    raise
