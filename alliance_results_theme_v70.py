from __future__ import annotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

VERSION="7.0-UNIFORM-RESULTS-THEME"
TARGET_PREFIXES=(
    "/whatsapp-live","/deal-match-ai-v60","/newspaper-v83","/property-database",
    "/requirements-workbench","/magazine-master-import","/inventory-activation",
    "/marketing-contacts","/hospitality","/retail"
)
CSS="""<style id='alliance-v70-theme'>
body{font-family:Inter,Arial,sans-serif!important;background:#efe4d2!important;color:#2d261f!important}
table{background:#fff!important;border-collapse:separate!important;border-spacing:0!important;border-radius:14px!important;overflow:hidden!important;box-shadow:0 5px 22px rgba(73,50,30,.10)!important}
th{background:#6a513d!important;color:#fff!important;font-size:12px!important;font-weight:800!important;padding:9px 10px!important;letter-spacing:.1px!important}
td{font-size:13px!important;line-height:1.42!important;padding:8px 10px!important;border-bottom:1px solid #eee3d8!important;vertical-align:top!important}
tr:hover td{background:#fff9f2!important}
.desc,.description,[class*='desc']{min-width:360px!important;max-width:560px!important;white-space:normal!important;font-size:13.5px!important;line-height:1.5!important}
.phone,.phoneLine{font-weight:800!important;white-space:nowrap!important}
.btn,button,a.btn{border-radius:8px!important;font-size:12px!important;padding:7px 10px!important}
.card{border-radius:14px!important;box-shadow:0 3px 14px rgba(73,50,30,.07)!important}
</style>"""
JS="""<script id='alliance-v70-js'>(function(){
function apply(){document.querySelectorAll('table').forEach(function(t){
 let hs=[...t.querySelectorAll('thead th, tr:first-child th')];
 hs.forEach(function(h,i){let z=(h.textContent||'').trim().toLowerCase();if(z==='id'||z==='record'||z==='record id'||z==='property id'||z==='requirement id'){
 h.style.display='none';t.querySelectorAll('tr').forEach(function(r){let c=r.children[i];if(c)c.style.display='none'})}})
});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply);else apply();
})();</script>"""

class ThemeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response=await call_next(request)
        p=request.url.path
        if not any(p.startswith(x) for x in TARGET_PREFIXES):return response
        ct=response.headers.get('content-type','')
        if 'text/html' not in ct:return response
        try:
            body=b''
            async for chunk in response.body_iterator: body+=chunk
            txt=body.decode('utf-8','replace')
            if 'alliance-v70-theme' not in txt:
                txt=txt.replace('</head>',CSS+'</head>') if '</head>' in txt else CSS+txt
                txt=txt.replace('</body>',JS+'</body>') if '</body>' in txt else txt+JS
            headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding'}}
            return Response(txt,status_code=response.status_code,headers=headers,media_type='text/html')
        except Exception:
            return response

def register(wrapped):
    app=wrapped.app
    if not getattr(app.state,'alliance_result_theme_v70',False):
        app.add_middleware(ThemeMiddleware);app.state.alliance_result_theme_v70=True
    @app.get('/api/v70/results-theme/status')
    def status():
        return {'status':'OK','version':VERSION,'technical_ids_hidden':True,'uniform_tables':True,'larger_font':True,'description_priority':True}
    return {'status':'REGISTERED','version':VERSION}
