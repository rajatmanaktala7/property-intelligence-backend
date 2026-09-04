from pathlib import Path
import re, shutil, time, py_compile
ROOT=Path(__file__).resolve().parent
f=ROOT/'alliance_magazine_fresh_v822.py'
g=ROOT/'alliance_magazine_safe_gateway_v660.py'
if not f.exists() or not g.exists(): raise SystemExit('Required Magazine files not found.')
fs=f.read_text(encoding='utf-8'); gs=g.read_text(encoding='utf-8')
if '/api/magazine-fresh/provider-test' not in fs or 'class ProviderGateway' not in gs:
    raise SystemExit('SAFETY STOP: expected 8.2.6 diagnostics/gateway not found.')
stamp=time.strftime('%Y%m%d-%H%M%S')
fb=ROOT/f'alliance_magazine_fresh_v822.py.before-v827-{stamp}.bak'
gb=ROOT/f'alliance_magazine_safe_gateway_v660.py.before-v827-{stamp}.bak'
shutil.copy2(f,fb); shutil.copy2(g,gb)

old='extra=(os.getenv("GEMINI_FALLBACK_MODELS") or "").strip()'
new='extra=(os.getenv("GEMINI_FALLBACK_MODELS") or "gemini-3.7-flash").strip()'
if old in gs: gs=gs.replace(old,new,1)

if 'def public_summary(self):' not in gs:
    marker='    def next_retry(self):'
    block='    def public_summary(self):\n        return [{"provider":p.get("label"),"kind":p.get("kind"),"available":self._available(p)} for p in self.providers]\n\n'
    if marker not in gs: raise SystemExit('SAFETY STOP: gateway retry marker missing.')
    gs=gs.replace(marker,block+marker,1)

gs=gs.replace('VERSION="6.6.0-ALLIANCE-MAGAZINE-SAFE-VISION-GATEWAY"',
              'VERSION="6.6.1-ALLIANCE-MAGAZINE-MULTIMODEL-OPENROUTER-GATEWAY"',1)
g.write_text(gs,encoding='utf-8')

fs=fs.replace('Fresh Magazine PDF Database · CRE OS 8.2.6','Fresh Magazine PDF Database · CRE OS 8.2.7')
fs=fs.replace('Provider diagnostics · automatic waterfall · safe checkpoint resume',
              'Multi-model Gemini · OpenRouter vision fallback · safe checkpoint resume')
fs=re.sub(r"VERSION='8\.2\.6[^']*'","VERSION='8.2.7-MULTIMODEL-OPENROUTER-FALLBACK'",fs,count=1)
fs=fs.replace("'message':'Provider waterfall ready' if configured else 'No vision provider configured'",
              "'message':'Multi-model provider waterfall ready' if configured else 'No vision provider configured'")
fs=fs.replace('"version":"8.2.6","tested":len(results)','"version":"8.2.7","tested":len(results)',1)
fs=fs.replace('"note":"One tiny live vision request was attempted per configured provider. No magazine pages were processed."',
              '"note":"One tiny live vision request was attempted per configured model/provider route. No magazine pages were processed."',1)
f.write_text(fs,encoding='utf-8')

try:
    py_compile.compile(str(f),doraise=True); py_compile.compile(str(g),doraise=True); py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(fb,f); shutil.copy2(gb,g); print('COMPILE FAILED - originals restored.'); raise

fc=f.read_text(encoding='utf-8'); gc=g.read_text(encoding='utf-8')
for req in ['CRE OS 8.2.7','MULTIMODEL-OPENROUTER-FALLBACK','/api/magazine-fresh/provider-test']:
    if req not in fc:
        shutil.copy2(fb,f); shutil.copy2(gb,g); raise SystemExit('SAFETY STOP: '+req+' missing; restored.')
if 'gemini-3.7-flash' not in gc or 'OPENROUTER_VISION_MODEL' not in gc:
    shutil.copy2(fb,f); shutil.copy2(gb,g); raise SystemExit('SAFETY STOP: fallback providers missing; restored.')
print('PASS: CRE OS 8.2.7 installed.')
print('Default: primary Gemini model -> gemini-3.7-flash fallback for each configured Gemini key -> optional OpenRouter vision.')
print('Overrides: GEMINI_FALLBACK_MODELS, OPENROUTER_API_KEY, OPENROUTER_VISION_MODEL.')
print('No API key values printed.')
print('Backups:',fb.name,gb.name)
