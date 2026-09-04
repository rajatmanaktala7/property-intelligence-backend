from pathlib import Path
import datetime, shutil, py_compile
root=Path(__file__).resolve().parent
entry=root/'production_entrypoint.py'
module=root/'alliance_magazine_fresh_v822.py'
if not entry.exists(): raise SystemExit('Missing production_entrypoint.py')
if not module.exists(): raise SystemExit('Missing alliance_magazine_fresh_v822.py')
text=entry.read_text(encoding='utf-8')
if 'alliance_magazine_fresh_v822' not in text:
    anchor='        CORE_APP = wrapped.app'
    if anchor not in text: raise SystemExit('SAFE STOP: CORE_APP anchor not found. No changes made.')
    block='''        # ALLIANCE_MAGAZINE_FRESH_V822_FINAL_ROUTE\n        try:\n            import alliance_magazine_fresh_v822 as magazine_fresh_v822\n            magazine_fresh_result = magazine_fresh_v822.register(wrapped.core)\n            stabilization = dict(stabilization or {})\n            stabilization["magazine_fresh_v822"] = magazine_fresh_result\n            print("[magazine-fresh-v822]", magazine_fresh_result)\n        except Exception as exc:\n            stabilization = dict(stabilization or {})\n            stabilization["magazine_fresh_v822"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}\n            print("[magazine-fresh-v822] warning:", type(exc).__name__, str(exc))\n\n'''
    backup=root/f'production_entrypoint.before-magazine-fresh-v822-{datetime.datetime.now().strftime("%Y%m%d-%H%M%S")}.py'
    shutil.copy2(entry,backup)
    entry.write_text(text.replace(anchor,block+anchor,1),encoding='utf-8')
py_compile.compile(str(module),doraise=True)
py_compile.compile(str(entry),doraise=True)
print('PASS: CRE OS 8.2.2 Fresh Magazine PDF installed')
