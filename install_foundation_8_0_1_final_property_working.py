from pathlib import Path
import py_compile, json, shutil, datetime
ROOT=Path(__file__).resolve().parent
TARGET=ROOT/'alliance_business_os_v800.py'
if not TARGET.exists(): raise SystemExit('alliance_business_os_v800.py missing')
import importlib.util
spec=importlib.util.spec_from_file_location('v801',TARGET); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
print('SELF TEST:',mod.self_test())
entry=ROOT/'production_entrypoint.py'
if not entry.exists(): raise SystemExit('production_entrypoint.py missing')
s=entry.read_text(encoding='utf-8')
if 'import alliance_business_os_v800 as alliance_business_os_v800' not in s:
    raise SystemExit('Alliance 8.0 registration not found in production_entrypoint.py. Deploy 8.0 first.')
for p in (TARGET,entry): py_compile.compile(str(p),doraise=True)
print('READY: final Property Database replacement compiled successfully')
print('MODULE:',TARGET.name)
print('ENTRYPOINT: unchanged')
print('Dockerfile: NOT MODIFIED')
