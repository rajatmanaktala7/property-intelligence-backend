from pathlib import Path
import shutil, time, py_compile

ROOT=Path(__file__).resolve().parent
f=ROOT/'alliance_magazine_fresh_v822.py'
if not f.exists():
    raise SystemExit('alliance_magazine_fresh_v822.py not found')

s=f.read_text(encoding='utf-8')
bak=ROOT/f"alliance_magazine_fresh_v822.py.before-v8241-{time.strftime('%Y%m%d-%H%M%S')}.bak"
shutil.copy2(f,bak)

decorator="    @app.get('/api/magazine-fresh/latest')"
needle="    @app.get('/api/magazine-fresh/status/{upload_id}')"
route='    @app.get(\'/api/magazine-fresh/latest\')\n    def latest(req:Request):\n        _login(core,req)\n        try:\n            with e.connect() as c:\n                rows=c.execute(text("""\n                    SELECT upload_id::text AS upload_id,\n                           filename,\n                           COALESCE(status,\'UNKNOWN\') AS status,\n                           COALESCE(page_count,0) AS page_count,\n                           COALESCE(processed_pages,0) AS processed_pages,\n                           COALESCE(created_records,0) AS created_records,\n                           COALESCE(review_records,0) AS review_records,\n                           error_message,\n                           created_at\n                    FROM pi_magazine_fresh_uploads\n                    ORDER BY created_at DESC NULLS LAST\n                    LIMIT 10\n                """)).mappings().all()\n            items=[]\n            for x in rows:\n                d=dict(x)\n                if d.get(\'created_at\') is not None:\n                    d[\'created_at\']=d[\'created_at\'].isoformat()\n                items.append(d)\n            return {\'status\':\'OK\',\'version\':\'8.2.4.1\',\'latest\':items[0] if items else None,\'uploads\':items}\n        except Exception as exc:\n            raise HTTPException(500,f\'Latest Magazine lookup failed: {type(exc).__name__}: {exc}\')\n\n'

if decorator not in s:
    if needle not in s:
        raise SystemExit('SAFETY STOP: status route marker not found. No file changed.')
    s=s.replace(needle,route+needle,1)

s=s.replace("VERSION='8.2.4-RESUME-DASHBOARD-MAGAZINE-PDF'",
            "VERSION='8.2.4.1-RESUME-DASHBOARD-LATEST-API-FIX'",1)

f.write_text(s,encoding='utf-8')
try:
    py_compile.compile(str(f),doraise=True)
    py_compile.compile(str(ROOT/'alliance_magazine_safe_gateway_v660.py'),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(bak,f)
    print('COMPILE FAILED - original restored:',bak)
    raise

check=f.read_text(encoding='utf-8')
if decorator not in check:
    shutil.copy2(bak,f)
    raise SystemExit('SAFETY STOP: latest API route was not registered. Original restored.')

print('PASS: CRE OS 8.2.4.1 latest stored Magazine API fixed')
print('Backup:',bak)
