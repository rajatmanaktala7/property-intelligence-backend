from pathlib import Path
import re, shutil, time, py_compile
ROOT=Path(__file__).resolve().parent
f=ROOT/'alliance_magazine_fresh_v822.py'
g=ROOT/'alliance_magazine_safe_gateway_v660.py'
if not f.exists() or not g.exists(): raise SystemExit('Required Magazine files not found.')
s=f.read_text(encoding='utf-8')
if 'ProviderGateway' not in s or '/api/magazine-fresh/resume/{upload_id}' not in s:
    raise SystemExit('SAFETY STOP: expected resilient Magazine processor is missing.')
bak=ROOT/f"alliance_magazine_fresh_v822.py.before-v825-{time.strftime('%Y%m%d-%H%M%S')}.bak"
shutil.copy2(f,bak)

needle="    @app.get('/api/magazine-fresh/status/{upload_id}')"
diag="    @app.get('/api/magazine-fresh/providers')\n    def providers(req:Request):\n        _login(core,req)\n        gw=safe_gateway.ProviderGateway()\n        configured=[p.get('label') for p in gw.providers]\n        return {'status':'OK','version':'8.2.5','configured_count':len(configured),\n                'providers':configured,\n                'gemini_keys':len({id(p.get('client')) for p in gw.providers if p.get('kind')=='gemini'}),\n                'openrouter_configured':any(p.get('kind')=='openrouter' for p in gw.providers),\n                'message':'Provider waterfall ready' if configured else 'No vision provider configured'}\n\n"
if "    @app.get('/api/magazine-fresh/providers')" not in s:
    if needle not in s: raise SystemExit('SAFETY STOP: status route marker missing.')
    s=s.replace(needle,diag+needle,1)

old="""        if row[1]=='PROCESSING':return {'status':'ALREADY_PROCESSING','upload_id':upload_id}
        bg.add_task(_process,core,upload_id)
        return {'status':'RESUME_STARTED','upload_id':upload_id,'version':'8.2.3'}"""
new="""        if row[1]=='PROCESSING':return {'status':'ALREADY_PROCESSING','upload_id':upload_id}
        with e.begin() as c:
            c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='QUEUED',error_message='Checking available AI providers...' WHERE upload_id=CAST(:u AS UUID)"),{'u':upload_id})
        bg.add_task(_process,core,upload_id)
        return {'status':'RESUME_STARTED','upload_id':upload_id,'version':'8.2.5','message':'Provider waterfall started'}"""
if old in s:s=s.replace(old,new,1)

old2='''retry=gw.next_retry();msg=meta.get("status","VISION_PROVIDER_UNAVAILABLE")
                if retry:msg+=" | retry_after="+retry.isoformat()
                with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='WAITING_FOR_PROVIDER',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':msg[:4000],'u':uid})'''
new2='''retry=gw.next_retry();reason=meta.get("status","VISION_PROVIDER_UNAVAILABLE")
                msg="AI provider unavailable. The PDF is safe and extraction can resume from page "+str(i)+"."
                if retry:msg+=" Next provider retry after "+retry.isoformat()+"."
                msg+=" Provider status: "+str(reason)+"."
                with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='WAITING_FOR_PROVIDER',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':msg[:1000],'u':uid})'''
if old2 in s:s=s.replace(old2,new2,1)

s=s.replace('Fresh Magazine PDF Database · CRE OS 8.2.4','Fresh Magazine PDF Database · CRE OS 8.2.5')
s=s.replace('Stored PDF recovery · resumable extraction · provider failover','Automatic provider waterfall · safe checkpoint resume · stored PDF recovery')
s=s.replace("state.textContent=d.error_message||'Ready.';","let em=d.error_message||'Ready.';if(String(em).includes('429')||String(em).includes('RESOURCE_EXHAUSTED'))em='AI quota exhausted on the previous provider. Click Resume Extraction to try the configured provider waterfall.';state.textContent=em;",1)
s=re.sub(r"VERSION='8\.2\.[^']*'","VERSION='8.2.5-PROVIDER-WATERFALL-AUTO-RESUME'",s,count=1)

f.write_text(s,encoding='utf-8')
try:
    py_compile.compile(str(f),doraise=True)
    py_compile.compile(str(g),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(bak,f); print('COMPILE FAILED - original restored:',bak); raise

check=f.read_text(encoding='utf-8')
for required in ["/api/magazine-fresh/providers","Provider waterfall started","WAITING_FOR_PROVIDER","CRE OS 8.2.5"]:
    if required not in check:
        shutil.copy2(bak,f); raise SystemExit('SAFETY STOP: missing '+required+'; original restored.')
print('PASS: CRE OS 8.2.5 Provider Waterfall installed')
print('Supports GEMINI_API_KEY through GEMINI_API_KEY_4, GEMINI_FALLBACK_MODELS, and optional OPENROUTER_API_KEY + OPENROUTER_VISION_MODEL.')
print('No secret values printed.')
print('Backup:',bak)
