from pathlib import Path
import shutil,time,py_compile

ROOT=Path(__file__).resolve().parent
m=ROOT/'alliance_magazine_fresh_v822.py'
if not m.exists():
    raise SystemExit('Required Magazine module missing.')
ms=m.read_text(encoding='utf-8')
if '8.2.8-SCHEMA-CONTRACT-TRANSIENT-RETRY' not in ms:
    raise SystemExit('SAFETY STOP: expected 8.2.8 baseline not found.')

stamp=time.strftime('%Y%m%d-%H%M%S')
bak=ROOT/f'alliance_magazine_fresh_v822.py.before-v829-{stamp}.bak'
shutil.copy2(m,bak)

marker="    @app.post('/api/magazine-fresh/provider-test')"
route='\n    @app.post(\'/api/magazine-fresh/real-page-test/{upload_id}\')\n    def real_page_test(upload_id:str,req:Request,page:int=Query(1,ge=1)):\n        _login(core,req)\n        with e.connect() as c:\n            row=c.execute(text("SELECT pdf_content,page_count FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)"),{\'u\':upload_id}).first()\n        if not row: raise HTTPException(404,\'Upload not found\')\n        if row[0] is None: raise HTTPException(409,\'Stored PDF not found\')\n        pdf=bytes(row[0])\n        doc=fitz.open(stream=pdf,filetype=\'pdf\')\n        try:\n            if page>len(doc): raise HTTPException(400,f\'Page out of range: {page}/{len(doc)}\')\n            p=doc.load_page(page-1)\n            scale=PDF_RENDER_DPI/72.0\n            jpg=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes(\'jpeg\')\n        finally:\n            doc.close()\n\n        gw=safe_gateway.ProviderGateway()\n        results=[]\n        for provider in gw.providers:\n            label=provider.get(\'label\',\'UNKNOWN\')\n            kind=provider.get(\'kind\',\'unknown\')\n            item={\'provider\':label,\'kind\':kind,\'image_bytes\':len(jpg)}\n            try:\n                data=gw._call_gemini(provider,jpg,PROMPT) if kind==\'gemini\' else gw._call_openrouter(provider,jpg,PROMPT)\n                item[\'transport\']=\'OK\'\n                raw=None\n                if isinstance(data,dict):\n                    raw=data.get(\'properties\')\n                    if raw is None:\n                        raw=data.get(\'records\')\n                item[\'has_properties_array\']=isinstance(raw,list)\n                item[\'record_count\']=len(raw) if isinstance(raw,list) else 0\n                item[\'top_level_keys\']=sorted([str(k) for k in data.keys()])[:20] if isinstance(data,dict) else []\n                item[\'result\']=\'OK\' if isinstance(raw,list) else \'JSON_SCHEMA_MISMATCH\'\n            except Exception as exc:\n                raw=str(exc)\n                upper=raw.upper()\n                if safe_gateway._is_daily_quota(exc):\n                    result=\'DAILY_QUOTA_EXHAUSTED\'\n                elif safe_gateway._is_quota(exc):\n                    result=\'RATE_LIMIT_OR_QUOTA\'\n                elif \'503\' in raw and (\'UNAVAILABLE\' in upper or \'HIGH DEMAND\' in upper):\n                    result=\'TRANSIENT_503\'\n                elif \'API_KEY\' in upper or \'API KEY\' in upper or \'UNAUTHENTICATED\' in upper or \'401\' in upper:\n                    result=\'AUTHENTICATION_ERROR\'\n                elif \'NOT_FOUND\' in upper or \'404\' in upper:\n                    result=\'MODEL_ACCESS_ERROR\'\n                elif \'JSON\' in upper or \'DECODE\' in upper or \'EXPECTING VALUE\' in upper:\n                    result=\'JSON_PARSE_ERROR\'\n                else:\n                    result=\'PROVIDER_ERROR\'\n                detail=re.sub(r\'AIza[0-9A-Za-z_-]{20,}\', \'[REDACTED_KEY]\', raw)\n                detail=re.sub(r\'(?i)(api[_ -]?key["=: ]+)[^ ,;}\\]]+\', r\'\\1[REDACTED]\', detail)\n                item.update({\'result\':result,\'detail\':detail[:1600]})\n            results.append(item)\n        return {\n            \'status\':\'OK\',\n            \'version\':\'8.2.9\',\n            \'page\':page,\n            \'render_dpi\':PDF_RENDER_DPI,\n            \'image_bytes\':len(jpg),\n            \'tested\':len(results),\n            \'results\':results,\n            \'note\':\'Exact stored Magazine page tested with production prompt. No records written and no checkpoint advanced.\'\n        }\n\n'
if marker not in ms:
    raise SystemExit('SAFETY STOP: provider-test route marker missing.')
if "/api/magazine-fresh/real-page-test/{upload_id}" not in ms:
    ms=ms.replace(marker,route+marker,1)

ms=ms.replace("VERSION='8.2.8-SCHEMA-CONTRACT-TRANSIENT-RETRY'","VERSION='8.2.9-REAL-PAGE-DIAGNOSTIC'",1)
ms=ms.replace('Fresh Magazine PDF Database · CRE OS 8.2.8','Fresh Magazine PDF Database · CRE OS 8.2.9')
ms=ms.replace('Schema-safe extraction · multi-model fallback · checkpoint resume','Real-page provider diagnosis · safe extraction · checkpoint resume')

m.write_text(ms,encoding='utf-8')
try:
    py_compile.compile(str(m),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(bak,m)
    print('COMPILE FAILED - original restored.')
    raise

mc=m.read_text(encoding='utf-8')
for token in ['8.2.9-REAL-PAGE-DIAGNOSTIC','/api/magazine-fresh/real-page-test/{upload_id}','No records written']:
    if token not in mc:
        shutil.copy2(bak,m)
        raise SystemExit('Validation failed; original restored.')

print('PASS: CRE OS 8.2.9 real-page diagnostic installed.')
print('No database mutation. No PDF mutation. No checkpoint change.')
print('Backup:',bak.name)
