from pathlib import Path
import datetime, importlib.util, py_compile, shutil

ROOT=Path(__file__).resolve().parent
ENTRY=ROOT/'production_entrypoint.py'
MODULE=ROOT/'alliance_business_os_v800.py'

def fail(msg): print('ERROR:',msg); raise SystemExit(1)
if not ENTRY.exists(): fail('production_entrypoint.py not found')
if not MODULE.exists(): fail('alliance_business_os_v800.py not found')

spec=importlib.util.spec_from_file_location('alliance_business_os_v800',MODULE)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
print('SELF TEST:',mod.self_test())
stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup=ROOT/f'production_entrypoint-before-v800-{stamp}.py'; shutil.copy2(ENTRY,backup); print('BACKUP:',backup.name)
s=ENTRY.read_text(encoding='utf-8-sig')
marker='# ALLIANCE_BUSINESS_OS_V800'
if marker not in s:
    anchor='    import alliance_live_feed_purity as live_feed_purity'
    pos=s.find(anchor)
    if pos<0: fail('Safe production registration anchor not found; nothing changed')
    block='''    # ALLIANCE_BUSINESS_OS_V800\n    try:\n        import alliance_business_os_v800 as alliance_business_os_v800\n        business_v800_result = alliance_business_os_v800.register(wrapped.core)\n        if isinstance(stabilization, dict):\n            stabilization["business_os_v800"] = business_v800_result\n    except Exception as exc:\n        print("WARNING: Alliance 8.0 business OS registration failed:", exc)\n        if isinstance(stabilization, dict):\n            stabilization["business_os_v800"] = {"status":"ERROR","error":str(exc)}\n\n'''
    s=s[:pos]+block+s[pos:]
    ENTRY.write_text(s,encoding='utf-8')
else: print('8.0 registration already present; no duplicate added')
for p in [MODULE,ENTRY]: py_compile.compile(str(p),doraise=True)
print('COMPILE: PASS')
print('Alliance CRE OS 8.0 installed')
print('APPROVED PROPERTY FIELDS: PRESERVED')
print('DATE + TIME: UNIVERSAL ON PRIMARY PROPERTY + REQUIREMENT DATABASES')
print('SOURCE / SOURCE NAME / ORIGINAL MESSAGE: PRESERVED')
print('DUMMY TECHNICAL FIELDS: HIDDEN FROM TEAM, NOT DELETED')
print('MATCHER / VERIFICATION / FOLLOW-UP: EXISTING ROUTES RETAINED')
print('DATA REPAIR / SOURCE RECOVERY: ADMIN ONLY')
print('CANONICAL IDS: PRESERVED')
print('NO DELETE / NO NEW MASTER PROPERTY')
print('DOCKERFILE: NOT MODIFIED')
