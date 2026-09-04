from pathlib import Path
import shutil,time,py_compile
ROOT=Path(__file__).resolve().parent
p=ROOT/"production_entrypoint.py"; m=ROOT/"alliance_magazine_fresh_v822.py"
if not p.exists() or not m.exists(): raise SystemExit("Required production files not found.")
ps=p.read_text(encoding="utf-8-sig"); ms=m.read_text(encoding="utf-8")
if "ALLIANCE_MAGAZINE_FRESH_V822_FINAL_ROUTE" not in ps or "CRE OS 8.2.7" not in ms:
    raise SystemExit("SAFETY STOP: expected 8.2.7 production baseline not found.")
stamp=time.strftime("%Y%m%d-%H%M%S")
pb=ROOT/f"production_entrypoint.py.before-v8271-{stamp}.bak"
mb=ROOT/f"alliance_magazine_fresh_v822.py.before-v8271-{stamp}.bak"
shutil.copy2(p,pb); shutil.copy2(m,mb)
old="def page(req:Request): _login(core,req); return HTMLResponse(_page())"
new="def page(req:Request): _login(core,req); return HTMLResponse(_page(),headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})"
if old in ms: ms=ms.replace(old,new,1)
old2="def alias(req:Request): _login(core,req); return HTMLResponse(_page())"
new2="def alias(req:Request): _login(core,req); return HTMLResponse(_page(),headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})"
if old2 in ms: ms=ms.replace(old2,new2,1)
m.write_text(ms,encoding="utf-8")
needle='            print("[magazine-fresh-v822]", magazine_fresh_result)\n'
insert='            # CRE OS 8.2.7.1: authoritative Magazine page route takeover.\n            magazine_routes = [\n                r for r in list(wrapped.app.router.routes)\n                if getattr(r, "path", None) == "/magazine-master-import"\n                and "GET" in set(getattr(r, "methods", set()) or set())\n                and getattr(getattr(r, "endpoint", None), "__module__", "") == "alliance_magazine_fresh_v822"\n            ]\n            if not magazine_routes:\n                raise RuntimeError("8.2.7 Magazine GET route was not registered")\n            chosen = magazine_routes[-1]\n            wrapped.app.router.routes.remove(chosen)\n            wrapped.app.router.routes.insert(0, chosen)\n            stabilization["magazine_route_takeover_v8271"] = {\n                "status": "AUTHORITATIVE",\n                "path": "/magazine-master-import",\n                "module": "alliance_magazine_fresh_v822",\n            }\n'
if "magazine_route_takeover_v8271" not in ps:
    if needle not in ps: raise SystemExit("SAFETY STOP: final Magazine registration marker missing.")
    ps=ps.replace(needle,needle+insert,1)
p.write_text(ps,encoding="utf-8")
try:
    py_compile.compile(str(p),doraise=True); py_compile.compile(str(m),doraise=True)
except Exception:
    shutil.copy2(pb,p); shutil.copy2(mb,m); print("COMPILE FAILED - originals restored."); raise
pc=p.read_text(encoding="utf-8-sig"); mc=m.read_text(encoding="utf-8")
if "magazine_route_takeover_v8271" not in pc or "no-store, no-cache" not in mc:
    shutil.copy2(pb,p); shutil.copy2(mb,m); raise SystemExit("SAFETY STOP: takeover validation failed; originals restored.")
print("PASS: CRE OS 8.2.7.1 Magazine route takeover installed.")
print("Stored PDF/database untouched. Fresh Magazine GET route forced first and no-cache.")
print("Backups:",pb.name,mb.name)
