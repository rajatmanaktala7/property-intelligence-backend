
from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

MODULE_VERSION="3.7.6-FINAL-FRESH-DASHBOARD"
FINAL_ROUTE="/team-dashboard-v376"

def _upgrade_html(html:str)->str:
    nav="""
    <div id="v376-nav" style="position:sticky;top:0;z-index:9999;background:#5d4937;color:#fff;
      padding:9px 14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
      box-shadow:0 3px 12px rgba(0,0,0,.15)">
      <a href="/team-dashboard-v376" style="color:#fff;text-decoration:none;font-weight:800;padding:8px 12px;background:#7a624d;border-radius:8px">← Dashboard</a>
      <a href="/workspace" style="color:#fff;text-decoration:none;font-weight:800;padding:8px 12px;background:#7a624d;border-radius:8px">Working Space</a>
      <a href="/whatsapp-live" style="color:#fff;text-decoration:none;font-weight:800;padding:8px 12px;background:#6b5746;border-radius:8px">WhatsApp Live</a>
      <a href="/whatsapp-live/sources" style="color:#fff;text-decoration:none;font-weight:800;padding:8px 12px;background:#6b5746;border-radius:8px">WhatsApp Sources</a>
      <span id="v376-fresh" style="margin-left:auto;font-weight:800">Checking data freshness…</span>
    </div>
    """
    if "<body>" in html:
        html=html.replace("<body>","<body>"+nav,1)
    else:
        html=nav+html

    panel="""
    <div class="card" id="v376-fresh-panel">
      <h2>Live Data Freshness</h2>
      <div id="v376-fresh-grid" class="template-grid"></div>
      <div id="v376-wa-warning" class="message" style="display:none"></div>
    </div>
    """
    marker='<section id="home">'
    if marker in html:
        html=html.replace(marker,marker+panel,1)

    js=r"""
    async function v376Fresh(){
      try{
        let r=await fetch('/api/team-dashboard-v376/freshness',{credentials:'include'});
        let d=await r.json();
        let w=d.whatsapp||{};
        let badge=w.status||'UNKNOWN';
        document.getElementById('v376-fresh').textContent='WhatsApp: '+badge+(w.latest_event_at?' · '+w.latest_event_at:'');
        let items=[
          ['WhatsApp',w.latest_event_at||'No event',badge],
          ['Newspaper',(d.newspaper||{}).latest||'No data',''],
          ['Magazine',(d.magazine||{}).latest||'No data',''],
          ['Hospitality',(d.hospitality||{}).latest||'No data',''],
          ['Retail',(d.retail||{}).latest||'No data',''],
          ['Manual Property',(d.manual||{}).latest||'No data','']
        ];
        document.getElementById('v376-fresh-grid').innerHTML=items.map(x=>
          `<div class="template-card" style="min-height:118px">
             <div><h3>${x[0]}</h3><p style="min-height:auto">${x[1]}</p></div>
             <div><b>${x[2]}</b></div>
           </div>`).join('');
        if(['STALE','NO_EVENTS','OFFLINE','ERROR'].includes(w.status)){
          let box=document.getElementById('v376-wa-warning');
          box.style.display='block';
          box.innerHTML='<b>WhatsApp intake is '+badge+'.</b> The Alliance backend only receives new posts when the external phone bridge calls <code>/whatsapp-live/api/ingest</code>. Check the phone bridge, approved mobile number and active group. <a href="/whatsapp-live/sources"><b>Open WhatsApp Sources</b></a>';
        }
      }catch(e){
        document.getElementById('v376-fresh').textContent='Freshness check failed';
      }
    }
    v376Fresh();
    setInterval(v376Fresh,60000);
    """
    html=html.replace("</script>",js+"</script>",1)
    return html

def register(app,engine,need_login):
    import team_dashboard_v374 as v374

    @app.get("/api/team-dashboard-v376/ui-status")
    def status(req:Request):
        need_login(req)
        return {
            "version":MODULE_VERSION,"status":"OK","dashboard":FINAL_ROUTE,
            "source_ui":v374.MODULE_VERSION,"back_navigation":True,"freshness_panel":True
        }

    @app.get(FINAL_ROUTE,response_class=HTMLResponse)
    def page(req:Request):
        need_login(req)
        return HTMLResponse(_upgrade_html(v374.DASHBOARD_HTML))

    @app.middleware("http")
    async def final_takeover(request,call_next):
        path=request.url.path.rstrip("/") or "/"
        if path in {
            "/workspace","/team-dashboard-live","/team-dashboard-v375",
            "/final-dashboard-v10","/final-dashboard-v11","/final-dashboard-v13"
        }:
            return RedirectResponse(FINAL_ROUTE,status_code=307)
        return await call_next(request)
