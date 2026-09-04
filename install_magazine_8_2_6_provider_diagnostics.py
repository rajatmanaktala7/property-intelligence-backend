from pathlib import Path
import re, shutil, time, py_compile
ROOT=Path(__file__).resolve().parent
f=ROOT/'alliance_magazine_fresh_v822.py'
g=ROOT/'alliance_magazine_safe_gateway_v660.py'
if not f.exists() or not g.exists(): raise SystemExit('Required Magazine files not found.')
s=f.read_text(encoding='utf-8')
if '/api/magazine-fresh/providers' not in s or 'safe_gateway.ProviderGateway' not in s:
    raise SystemExit('SAFETY STOP: CRE OS 8.2.5 provider waterfall not found.')
bak=ROOT/f"alliance_magazine_fresh_v822.py.before-v826-{time.strftime('%Y%m%d-%H%M%S')}.bak"
shutil.copy2(f,bak)
marker="    @app.get('/api/magazine-fresh/providers')"
route='    @app.post(\'/api/magazine-fresh/provider-test\')\n    def provider_test(req:Request):\n        _login(core,req)\n        gw=safe_gateway.ProviderGateway()\n        results=[]\n        try:\n            from PIL import Image\n            import io\n            im=Image.new("RGB",(24,24),"white")\n            b=io.BytesIO(); im.save(b,format="JPEG",quality=60); img=b.getvalue()\n        except Exception as exc:\n            raise HTTPException(500,f"Diagnostic image creation failed: {type(exc).__name__}: {exc}")\n        prompt=\'Return JSON only: {"diagnostic":"OK"}. This is a provider connectivity test.\'\n        for p in gw.providers:\n            label=p.get("label","UNKNOWN"); kind=p.get("kind","unknown")\n            try:\n                data=gw._call_gemini(p,img,prompt) if kind=="gemini" else gw._call_openrouter(p,img,prompt)\n                results.append({"provider":label,"kind":kind,"result":"OK","detail":"Provider accepted a live vision request."})\n            except Exception as exc:\n                raw=str(exc); upper=raw.upper()\n                if safe_gateway._is_daily_quota(exc): result="DAILY_QUOTA_EXHAUSTED"\n                elif safe_gateway._is_quota(exc): result="RATE_LIMIT_OR_QUOTA"\n                elif "API_KEY" in upper or "API KEY" in upper or "UNAUTHENTICATED" in upper or "401" in upper: result="AUTHENTICATION_ERROR"\n                elif "NOT_FOUND" in upper or "404" in upper: result="MODEL_ACCESS_ERROR"\n                else: result="PROVIDER_ERROR"\n                detail=re.sub(r\'AIza[0-9A-Za-z_-]{20,}\', \'[REDACTED_KEY]\', raw)\n                detail=re.sub(r\'(?i)(api[_ -]?key["=: ]+)[^ ,;}\\]]+\', r\'\\1[REDACTED]\', detail)\n                results.append({"provider":label,"kind":kind,"result":result,"detail":detail[:1200]})\n        return {"status":"OK","version":"8.2.6","tested":len(results),"results":results,\n                "note":"One tiny live vision request was attempted per configured provider. No magazine pages were processed."}\n\n'
if "    @app.post('/api/magazine-fresh/provider-test')" not in s:
    if marker not in s: raise SystemExit('SAFETY STOP: provider route marker missing.')
    s=s.replace(marker,route+marker,1)
s=s.replace('Fresh Magazine PDF Database · CRE OS 8.2.5','Fresh Magazine PDF Database · CRE OS 8.2.6')
s=s.replace('Automatic provider waterfall · safe checkpoint resume · stored PDF recovery','Provider diagnostics · automatic waterfall · safe checkpoint resume')
s=re.sub(r"VERSION='8\.2\.5[^']*'","VERSION='8.2.6-PROVIDER-DIAGNOSTICS'",s,count=1)
f.write_text(s,encoding='utf-8')
try:
    py_compile.compile(str(f),doraise=True)
    py_compile.compile(str(g),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(bak,f); print('COMPILE FAILED - original restored:',bak); raise
check=f.read_text(encoding='utf-8')
for required in ['/api/magazine-fresh/provider-test','CRE OS 8.2.6','PROVIDER-DIAGNOSTICS']:
    if required not in check:
        shutil.copy2(bak,f); raise SystemExit('SAFETY STOP: missing '+required+'; original restored.')
print('PASS: CRE OS 8.2.6 Provider Diagnostics installed.')
print('POST /api/magazine-fresh/provider-test performs one tiny live vision request per configured provider.')
print('No Magazine pages are processed and API key values are never printed.')
print('Backup:',bak)
