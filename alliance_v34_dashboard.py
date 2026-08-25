
from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION="3.4.0-UNIFIED-INTELLIGENCE-DASHBOARD"

DASHBOARD_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance AI V3 Control Centre</title>
<style>
:root{
 --bg:#f4f6f8;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;
 --good:#067647;--warn:#b54708;--bad:#b42318;--nav:#101828;--soft:#f9fafb;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);font-family:Inter,Arial,sans-serif;color:var(--ink)}
.top{background:var(--nav);color:white;padding:18px 24px;position:sticky;top:0;z-index:3}
.top h1{margin:0;font-size:22px}.top small{color:#d0d5dd}
.wrap{max-width:1320px;margin:22px auto;padding:0 18px 36px}
.hero{background:linear-gradient(135deg,#101828,#344054);color:white;border-radius:18px;padding:26px;margin-bottom:18px}
.hero h2{margin:0 0 8px;font-size:28px}.hero p{margin:0;color:#eaecf0}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 2px 8px #10182808}
.card h3{margin:0 0 6px;font-size:16px}.big{font-size:28px;font-weight:800;margin:8px 0}.muted{color:var(--muted);font-size:13px}
.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#f2f4f7;font-size:12px;font-weight:700}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.section{margin-top:22px}.section h2{font-size:20px;margin:0 0 12px}
.actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
a.tile{display:block;text-decoration:none;color:inherit;background:white;border:1px solid var(--line);border-radius:14px;padding:16px}
a.tile:hover{border-color:#98a2b3;box-shadow:0 3px 10px #10182810}
.tile b{display:block;margin-bottom:4px}.tile span{font-size:13px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:white;border-radius:14px;overflow:hidden}
th,td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
th{background:var(--soft);font-size:12px;text-transform:uppercase;color:#475467}
button{border:0;border-radius:9px;padding:10px 14px;background:#101828;color:white;font-weight:700;cursor:pointer}
button.secondary{background:white;color:#101828;border:1px solid var(--line)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
#log{white-space:pre-wrap;background:#101828;color:#d1fadf;border-radius:12px;padding:12px;font-size:12px;max-height:260px;overflow:auto}
@media(max-width:950px){.grid{grid-template-columns:repeat(2,1fr)}.actions{grid-template-columns:repeat(2,1fr)}}
@media(max-width:620px){.grid,.actions{grid-template-columns:1fr}.top{position:static}.hero h2{font-size:23px}}
</style>
</head>
<body>
<div class="top">
  <h1>Alliance AI V3 Control Centre</h1>
  <small>Hospitality · Retail Expansion · Contact Vault · WhatsApp Marketing</small>
</div>
<div class="wrap">
  <div class="hero">
    <h2>Unified Intelligence Dashboard</h2>
    <p>One place for contact intelligence, retail expansion signals, hospitality data, phone imports and source-segregated marketing lists.</p>
  </div>

  <div class="grid">
    <div class="card"><h3>Hospitality Database</h3><div class="big" id="hospitalityCount">—</div><div class="muted" id="hospitalityState">Checking…</div></div>
    <div class="card"><h3>Retail Expansion Signals</h3><div class="big" id="retailSignals">—</div><div class="muted" id="retailState">Checking…</div></div>
    <div class="card"><h3>Contact Vault</h3><div class="big" id="contactCount">—</div><div class="muted" id="contactState">Checking…</div></div>
    <div class="card"><h3>Phone Import</h3><div class="big" id="importState">—</div><div class="muted">VCF · CSV · XLSX</div></div>
  </div>

  <div class="section">
    <h2>Core Workspaces</h2>
    <div class="actions">
      <a class="tile" href="/v3/hospitality-intelligence"><b>Hospitality Intelligence</b><span>Restaurants, cafes, lounges, clubs, banquets, hotels and guest houses.</span></a>
      <a class="tile" href="/v3/retail-expansion-intelligence"><b>Retail Expansion</b><span>Expansion signals, decision makers and requirement intelligence.</span></a>
      <a class="tile" href="/v3/contact-import"><b>Import Contacts from Phone</b><span>Upload VCF/vCard, CSV or XLSX into the permanent Contact Vault.</span></a>
      <a class="tile" href="/api/v3/contacts?category=CAFE&limit=200"><b>Cafe Contacts</b><span>Separate Cafe database.</span></a>
      <a class="tile" href="/api/v3/contacts?category=LOUNGE&limit=200"><b>Lounge Contacts</b><span>Separate Lounge database.</span></a>
      <a class="tile" href="/api/v3/contacts?category=RESTAURANT&limit=200"><b>Restaurant Contacts</b><span>Restaurant contact database.</span></a>
      <a class="tile" href="/api/v3/contacts?category=BANQUET&limit=200"><b>Banquet Contacts</b><span>Banquet contact database.</span></a>
      <a class="tile" href="/api/v3/contacts?bucket=WHATSAPP_GROUP&limit=200"><b>WhatsApp Group Database</b><span>Only contacts originating from WhatsApp-group sources.</span></a>
      <a class="tile" href="/api/v3/contacts?bucket=MAGAZINE&limit=200"><b>Magazine Contacts</b><span>Contacts derived from magazine databases.</span></a>
      <a class="tile" href="/api/v3/contacts?bucket=NEWSPAPER&limit=200"><b>Newspaper Contacts</b><span>Contacts derived from newspaper databases.</span></a>
      <a class="tile" href="/api/v3/contacts?bucket=PHONE_IMPORT&limit=200"><b>Phone Imports</b><span>Contacts manually imported from team phones.</span></a>
      <a class="tile" href="/api/v3/contacts/whatsapp-ready?limit=500"><b>WhatsApp-Ready</b><span>Verified + approved/opted-in + not DND only.</span></a>
    </div>
  </div>

  <div class="section">
    <h2>Retail Expansion Intelligence</h2>
    <div class="actions">
      <a class="tile" href="/api/v3/retail/signals?limit=100"><b>Expansion Signals</b><span>Current retail-news and expansion evidence.</span></a>
      <a class="tile" href="/api/v3/retail/contacts?limit=100"><b>Decision-Maker Contacts</b><span>BD, Expansion, Leasing, Real Estate and Store Development roles.</span></a>
      <a class="tile" href="/api/v3/retail/runs?limit=50"><b>Bot Run History</b><span>Discovery execution history and provider status.</span></a>
      <a class="tile" href="/api/v3/retail/v32b/purity-preview?limit=50"><b>Purity Review</b><span>Reject stale and generic retail content.</span></a>
      <a class="tile" href="/api/v3/retail/v32c1/status"><b>Expansion Stage Engine</b><span>Future Expansion, Active Rollout, Store Opened and stale-content stages.</span></a>
    </div>
  </div>

  <div class="section">
    <h2>Live Source Summary</h2>
    <div class="row" style="margin-bottom:10px">
      <button onclick="refreshAll()">Refresh Dashboard</button>
      <button class="secondary" onclick="loadSourceCounts()">Refresh Source Counts</button>
    </div>
    <table>
      <thead><tr><th>Database</th><th>Count</th><th>Purpose</th></tr></thead>
      <tbody>
        <tr><td>Cafe</td><td id="cafeCount">—</td><td>Hospitality/WhatsApp marketing</td></tr>
        <tr><td>Lounge</td><td id="loungeCount">—</td><td>Hospitality/WhatsApp marketing</td></tr>
        <tr><td>WhatsApp Groups</td><td id="waGroupCount">—</td><td>Imported/extracted WhatsApp-source contacts</td></tr>
        <tr><td>Magazine</td><td id="magCount">—</td><td>Magazine-source contacts</td></tr>
        <tr><td>Newspaper</td><td id="newsCount">—</td><td>Newspaper-source contacts</td></tr>
        <tr><td>Phone Import</td><td id="phoneCount">—</td><td>Manual phone exports</td></tr>
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>System Log</h2>
    <div id="log">Dashboard ready.</div>
  </div>
</div>

<script>
const log=(m)=>{document.getElementById('log').textContent=new Date().toLocaleTimeString()+"  "+m+"\n"+document.getElementById('log').textContent}
async function j(url){
  try{
    const r=await fetch(url,{credentials:'include'});
    const t=await r.text();
    let d={}; try{d=JSON.parse(t)}catch(e){d={raw:t}}
    return {ok:r.ok,status:r.status,data:d};
  }catch(e){return {ok:false,status:0,data:{error:String(e)}}}
}
async function refreshAll(){
  const h=await j('/api/v3/hospitality/status');
  document.getElementById('hospitalityCount').textContent=h.data.entities ?? '—';
  document.getElementById('hospitalityState').textContent=h.ok ? (h.data.schema_ready===false?'Setup required':'Healthy') : 'Unavailable';

  const r=await j('/api/v3/retail/db-status');
  document.getElementById('retailSignals').textContent=r.data.expansion_signals ?? '—';
  document.getElementById('retailState').textContent=r.ok ? (r.data.schema_ready===false?'Setup required':'Healthy') : 'Unavailable';

  const c=await j('/api/v3/contacts?limit=1');
  document.getElementById('contactState').textContent=c.ok?'Healthy':'Unavailable';
  if(c.ok){
    const all=await j('/api/v3/contacts?limit=2000');
    document.getElementById('contactCount').textContent=all.data.count ?? '—';
  }

  const i=await j('/api/v3/contacts/import/status');
  document.getElementById('importState').textContent=i.ok?'READY':'OFFLINE';
  await loadSourceCounts();
  log('Dashboard refreshed');
}
async function count(url,id){
  const x=await j(url);
  document.getElementById(id).textContent=x.data.count ?? '—';
}
async function loadSourceCounts(){
  await Promise.all([
    count('/api/v3/contacts?category=CAFE&limit=2000','cafeCount'),
    count('/api/v3/contacts?category=LOUNGE&limit=2000','loungeCount'),
    count('/api/v3/contacts?bucket=WHATSAPP_GROUP&limit=2000','waGroupCount'),
    count('/api/v3/contacts?bucket=MAGAZINE&limit=2000','magCount'),
    count('/api/v3/contacts?bucket=NEWSPAPER&limit=2000','newsCount'),
    count('/api/v3/contacts?bucket=PHONE_IMPORT&limit=2000','phoneCount'),
  ]);
}
refreshAll();
</script>
</body>
</html>"""

def register(core):
    app=core.app

    @app.get("/api/v3/dashboard/status")
    def dashboard_status(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "db_access":False,
            "startup_schema_ddl":False,
            "dashboard":"/v3/control-centre",
            "legacy_dashboard_untouched":True,
        }

    @app.get("/v3/control-centre",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse(DASHBOARD_HTML)
