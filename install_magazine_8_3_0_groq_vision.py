from pathlib import Path
from datetime import datetime
import py_compile, shutil

ROOT=Path(__file__).resolve().parent
GW=ROOT/'alliance_magazine_safe_gateway_v660.py'
FRESH=ROOT/'alliance_magazine_fresh_v822.py'
STAMP=datetime.now().strftime('%Y%m%d-%H%M%S')

def rep(s,a,b,label):
    if a not in s: raise RuntimeError('Patch point not found: '+label)
    return s.replace(a,b,1)

if not GW.exists() or not FRESH.exists():
    raise SystemExit('Required Alliance Magazine files not found.')

gb=GW.with_name(GW.name+f'.before-8.3.0-{STAMP}.bak')
fb=FRESH.with_name(FRESH.name+f'.before-8.3.0-{STAMP}.bak')
shutil.copy2(GW,gb); shutil.copy2(FRESH,fb)

try:
    g=GW.read_text(encoding='utf-8'); f=FRESH.read_text(encoding='utf-8')
    a='        # Optional OpenRouter multimodal fallback. No dependency change: httpx already exists.\n        ork=(os.getenv("OPENROUTER_API_KEY") or "").strip()\n'
    b='        # Groq vision fallback using Groq OpenAI-compatible HTTPS API.\n        gk=(os.getenv("GROQ_API_KEY") or "").strip()\n        gm=(os.getenv("GROQ_VISION_MODEL") or "qwen/qwen3.6-27b").strip()\n        if gk and gm:\n            self.providers.append({"kind":"groq","label":f"GROQ:{gm}","api_key":gk,"model":gm})\n\n        # Optional OpenRouter multimodal fallback. No dependency change: httpx already exists.\n        ork=(os.getenv("OPENROUTER_API_KEY") or "").strip()\n'
    g=rep(g,a,b,'provider registration')
    a='    def _call_openrouter(self,p,img,prompt):\n'
    b='''    def _call_groq(self,p,img,prompt):
        b64=base64.b64encode(img).decode("ascii")
        payload={"model":p["model"],"temperature":0,"reasoning_effort":"none","response_format":{"type":"json_object"},"messages":[{"role":"user","content":[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}]}]}
        with httpx.Client(timeout=120.0) as h:
            r=h.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+p["api_key"],"Content-Type":"application/json"},json=payload)
            if r.status_code==429: raise RuntimeError("429 GROQ_QUOTA "+r.text[:1000])
            r.raise_for_status()
            return _json_text(r.json()["choices"][0]["message"]["content"])

    def _call_provider(self,p,img,prompt):
        if p["kind"]=="gemini": return self._call_gemini(p,img,prompt)
        if p["kind"]=="groq": return self._call_groq(p,img,prompt)
        if p["kind"]=="openrouter": return self._call_openrouter(p,img,prompt)
        raise RuntimeError("Unsupported vision provider: "+str(p.get("kind")))

    def _call_openrouter(self,p,img,prompt):
'''
    g=rep(g,a,b,'Groq call')
    old='data=self._call_gemini(p,img,prompt) if p["kind"]=="gemini" else self._call_openrouter(p,img,prompt)'
    if g.count(old)<2: raise RuntimeError('Gateway dispatch points not found.')
    g=g.replace(old,'data=self._call_provider(p,img,prompt)')
    g=g.replace('VERSION="6.6.2-ALLIANCE-MAGAZINE-SCHEMA-TRANSIENT-RETRY-GATEWAY"','VERSION="8.3.0-ALLIANCE-MAGAZINE-GROQ-VISION-WATERFALL"',1)
    f=rep(f,"data=gw._call_gemini(provider,jpg,PROMPT) if kind=='gemini' else gw._call_openrouter(provider,jpg,PROMPT)","data=gw._call_provider(provider,jpg,PROMPT)",'real-page diagnostic')
    f=rep(f,'data=gw._call_gemini(p,img,prompt) if kind=="gemini" else gw._call_openrouter(p,img,prompt)','data=gw._call_provider(p,img,prompt)','provider diagnostic')
    old="                'openrouter_configured':any(p.get('kind')=='openrouter' for p in gw.providers),\n                'message':'Multi-model provider waterfall ready' if configured else 'No vision provider configured'}"
    new="                'groq_configured':any(p.get('kind')=='groq' for p in gw.providers),\n                'openrouter_configured':any(p.get('kind')=='openrouter' for p in gw.providers),\n                'message':'Gemini -> Groq -> OpenRouter provider waterfall ready' if configured else 'No vision provider configured'}"
    f=rep(f,old,new,'provider summary')
    f=f.replace("VERSION='8.2.9-REAL-PAGE-DIAGNOSTIC'","VERSION='8.3.0-GROQ-VISION-WATERFALL'",1)
    f=f.replace('Fresh Magazine PDF Database · CRE OS 8.2.9','Fresh Magazine PDF Database · CRE OS 8.3.0')
    f=f.replace('Real-page provider diagnosis · safe extraction · checkpoint resume','Gemini -> Groq Vision -> OpenRouter · real-page validation · checkpoint resume')
    GW.write_text(g,encoding='utf-8'); FRESH.write_text(f,encoding='utf-8')
    py_compile.compile(str(GW),doraise=True); py_compile.compile(str(FRESH),doraise=True); py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
    print('CRE OS 8.3.0 installed successfully.')
    print('PDF/database/checkpoints/Dockerfile/startup untouched.')
except Exception:
    shutil.copy2(gb,GW); shutil.copy2(fb,FRESH)
    print('FAILED - originals restored.')
    raise
