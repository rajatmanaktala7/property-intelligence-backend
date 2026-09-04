from __future__ import annotations
VERSION="8.7.0-FINAL-DATABASE-GRID-UI"
GRID_CSS="""<style id="alliance-final-grid-v870">
:root{--grid:#98a2b3;--grid-dark:#667085;--head:#e9eef5;--row:#fff;--alt:#f8fafc}
.tablebox,.alliance-grid-wrap{overflow:auto!important;max-height:76vh!important;border:1px solid var(--grid-dark)!important;border-radius:2px!important;background:#fff!important;padding:0!important}
table{border-collapse:collapse!important;border-spacing:0!important;width:max-content!important;min-width:100%!important;font-size:12px!important;line-height:1.25!important;background:#fff!important}
thead{position:sticky!important;top:0!important;z-index:4!important}
th{background:var(--head)!important;font-weight:700!important;color:#101828!important;white-space:nowrap!important;border:1px solid var(--grid-dark)!important;padding:7px 8px!important;vertical-align:middle!important}
td{background:var(--row)!important;color:#101828!important;border:1px solid var(--grid)!important;padding:6px 8px!important;vertical-align:top!important;white-space:normal!important}
tbody tr:nth-child(even) td{background:var(--alt)!important}
tbody tr:hover td{background:#eef4ff!important}
td .desc,.desc{min-width:280px!important;max-width:420px!important;white-space:normal!important}
td:first-child,th:first-child{min-width:155px!important}
button,.btn,.summarybtn{white-space:nowrap!important}
.tablebox table a{font-weight:600}
@media(max-width:900px){table{font-size:11px!important}th,td{padding:5px 6px!important}.tablebox{max-height:72vh!important}}
</style>"""
SCRIPT="""<script id="alliance-final-grid-script-v870">
(function(){function apply(){document.querySelectorAll('.tablebox').forEach(function(x){x.classList.add('alliance-grid-wrap')});document.querySelectorAll('table').forEach(function(t){t.classList.add('alliance-final-grid')})}if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',apply)}else{apply()}})();
</script>"""
def _app(core): return getattr(core,"app",None) or core
def _inject(body):
    if not isinstance(body,(bytes,bytearray)): return body
    s=body.decode("utf-8","ignore")
    if "alliance-final-grid-v870" in s:return body
    s=s.replace("</head>",GRID_CSS+"</head>",1) if "</head>" in s else GRID_CSS+s
    s=s.replace("</body>",SCRIPT+"</body>",1) if "</body>" in s else s+SCRIPT
    return s.encode()
class GridMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope.get("type")!="http":return await self.app(scope,receive,send)
        path=scope.get("path","")
        targets=("/alliance/primary","/magazine-organizer","/magazine-complete","/magazine-fastlane/records")
        if not any(path==x or path.startswith(x+"/") for x in targets):return await self.app(scope,receive,send)
        start=None; chunks=[]
        async def capture(message):
            nonlocal start
            if message["type"]=="http.response.start":start=message;return
            if message["type"]=="http.response.body":
                chunks.append(message.get("body",b""))
                if message.get("more_body",False):return
                body=b"".join(chunks)
                ctype=dict(start.get("headers",[])).get(b"content-type",b"").decode("latin1").lower()
                if "text/html" in ctype:
                    body=_inject(body)
                    start["headers"]=[(k,v) for k,v in start.get("headers",[]) if k.lower()!=b"content-length"]+[(b"content-length",str(len(body)).encode())]
                await send(start);await send({"type":"http.response.body","body":body,"more_body":False})
        await self.app(scope,receive,capture)
def register(core):
    app=_app(core)
    if app is None:raise RuntimeError("Alliance final grid requires app")
    marker="alliance_final_database_grid_v870"
    if getattr(app.state,marker,False):return {"status":"ALREADY_REGISTERED","version":VERSION}
    app.add_middleware(GridMiddleware);setattr(app.state,marker,True)
    return {"status":"REGISTERED","version":VERSION,"data_changes":False,"field_changes":False}
