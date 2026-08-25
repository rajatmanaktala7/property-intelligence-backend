
from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION="3.4A-LAZY-DASHBOARD-STARTUP-SAFE"
_MOUNTED=False

def _html():
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance AI V3 Control Centre</title>
<style>
body{margin:0;background:#f4f6f8;font-family:Arial,sans-serif;color:#172033}
.top{background:#101828;color:#fff;padding:18px 24px}
.top h1{margin:0;font-size:22px}.top small{color:#d0d5dd}
.wrap{max-width:1280px;margin:22px auto;padding:0 18px 36px}
.hero{background:#fff;border-radius:16px;padding:22px;margin-bottom:18px;border:1px solid #e4e7ec}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.card,.tile{background:#fff;border:1px solid #e4e7ec;border-radius:14px;padding:16px}
.card .big{font-size:27px;font-weight:800;margin-top:8px}
.muted{font-size:13px;color:#667085}
.section{margin-top:22px}.section h2{font-size:20px}
.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.tile{text-decoration:none;color:#172033;display:block}
.tile b{display:block;margin-bottom:5px}.tile span{font-size:13px;color:#667085}
table{width:100%;border-collapse:collapse;background:#fff}
th,td{padding:10px;border-bottom:1px solid #e4e7ec;text-align:left;font-size:13px}
th{background:#f9fafb}
button{background:#101828;color:#fff;border:0;border-radius:8px;padding:10px 14px;font-weight:700}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.actions{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.grid,.actions{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="top"><h1>Alliance AI V3 Control Centre</h1>
<small>Hospitality · Retail Expansion · Contact Vault · WhatsApp Marketing</small></div>
<div class="wrap">
<div class="hero"><h2>Unified Intelligence Dashboard</h2>
<p>Operate the new V3 system from one screen. Existing legacy dashboards remain untouched.</p></div>

<div class="grid">
<div class="card"><b>Hospitality</b><div class="big" id="hospitality">—</div><div class="muted">Persistent hospitality entities</div></div>
<div class="card"><b>Retail Expansion</b><div class="big" id="retail">—</div><div class="muted">Expansion signals</div></div>
<div class="card"><b>Contact Vault</b><div class="big" id="contacts">—</div><div class="muted">Segregated contacts</div></div>
<div class="card"><b>Phone Import</b><div class="big" id="phoneStatus">—</div><div class="muted">VCF · CSV · XLSX</div></div>
</div>

<div class="section"><h2>Workspaces</h2><div class="actions">
<a class="tile" href="/v3/hospitality-intelligence"><b>Hospitality Intelligence</b><span>Restaurants, cafes, lounges, clubs, banquets, hotels and guest houses.</span></a>
<a class="tile" href="/v3/retail-expansion-intelligence"><b>Retail Expansion</b><span>Expansion news, decision makers and requirements.</span></a>
<a class="tile" href="/v3/contact-import"><b>Import Contacts from Phone</b><span>Upload VCF, CSV or XLSX.</span></a>
<a class="tile" href="/api/v3/contacts?category=CAFE&limit=200"><b>Cafe Contacts</b><span>Separate Cafe contact database.</span></a>
<a class="tile" href="/api/v3/contacts?category=LOUNGE&limit=200"><b>Lounge Contacts</b><span>Separate Lounge contact database.</span></a>
<a class="tile" href="/api/v3/contacts?category=RESTAURANT&limit=200"><b>Restaurant Contacts</b><span>Restaurant database.</span></a>
<a class="tile" href="/api/v3/contacts?category=BANQUET&limit=200"><b>Banquet Contacts</b><span>Banquet database.</span></a>
<a class="tile" href="/api/v3/contacts?bucket=WHATSAPP_GROUP&limit=200"><b>WhatsApp Groups</b><span>Only WhatsApp-source contacts.</span></a>
<a class="tile" href="/api/v3/contacts?bucket=MAGAZINE&limit=200"><b>Magazine Contacts</b><span>Only magazine-source contacts.</span></a>
<a class="tile" href="/api/v3/contacts?bucket=NEWSPAPER&limit=200"><b>Newspaper Contacts</b><span>Only newspaper-source contacts.</span></a>
<a class="tile" href="/api/v3/contacts?bucket=PHONE_IMPORT&limit=200"><b>Phone Imports</b><span>Contacts imported manually from phones.</span></a>
<a class="tile" href="/api/v3/contacts/whatsapp-ready?limit=500"><b>WhatsApp-Ready</b><span>Verified, approved/opted-in and not DND.</span></a>
</div></div>

<div class="section"><h2>Retail Intelligence</h2><div class="actions">
<a class="tile" href="/api/v3/retail/signals?limit=100"><b>Expansion Signals</b><span>Retail expansion evidence.</span></a>
<a class="tile" href="/api/v3/retail/contacts?limit=100"><b>Decision Makers</b><span>Expansion, leasing, real estate and BD roles.</span></a>
<a class="tile" href="/api/v3/retail/runs?limit=50"><b>Bot Runs</b><span>Discovery run history.</span></a>
<a class="tile" href="/api/v3/retail/v32b/purity-preview?limit=50"><b>Purity Review</b><span>Stale/generic signal review.</span></a>
<a class="tile" href="/api/v3/retail/v32c1/status"><b>Expansion Stage Engine</b><span>Future / active / opened / stale stages.</span></a>
</div></div>

<div class="section"><h2>Source Summary</h2>
<button onclick="loadAll()">Refresh</button>
<table style="margin-top:10px"><thead><tr><th>Source</th><th>Count</th></tr></thead><tbody>
<tr><td>Cafe</td><td id="cafe">—</td></tr>
<tr><td>Lounge</td><td id="lounge">—</td></tr>
<tr><td>WhatsApp Groups</td><td id="wa">—</td></tr>
<tr><td>Magazine</td><td id="mag">—</td></tr>
<tr><td>Newspaper</td><td id="news">—</td></tr>
<tr><td>Phone Imports</td><td id="phone">—</td></tr>
</tbody></table></div>
</div>
<script>
async function getj(url){
 try{let r=await fetch(url,{credentials:'include'});let t=await r.text();let d={};try{d=JSON.parse(t)}catch(e){};return {ok:r.ok,d:d}}
 catch(e){return {ok:false,d:{}}}
}
async function setCount(url,id){
 let x=await getj(url);document.getElementById(id).textContent=x.d.count ?? '—';
}
async function loadAll(){
 let h=await getj('/api/v3/hospitality/status');
 document.getElementById('hospitality').textContent=h.d.entities ?? '—';
 let r=await getj('/api/v3/retail/db-status');
 document.getElementById('retail').textContent=r.d.expansion_signals ?? '—';
 let c=await getj('/api/v3/contacts?limit=2000');
 document.getElementById('contacts').textContent=c.d.count ?? '—';
 let p=await getj('/api/v3/contacts/import/status');
 document.getElementById('phoneStatus').textContent=p.ok?'READY':'OFFLINE';
 await Promise.all([
  setCount('/api/v3/contacts?category=CAFE&limit=2000','cafe'),
  setCount('/api/v3/contacts?category=LOUNGE&limit=2000','lounge'),
  setCount('/api/v3/contacts?bucket=WHATSAPP_GROUP&limit=2000','wa'),
  setCount('/api/v3/contacts?bucket=MAGAZINE&limit=2000','mag'),
  setCount('/api/v3/contacts?bucket=NEWSPAPER&limit=2000','news'),
  setCount('/api/v3/contacts?bucket=PHONE_IMPORT&limit=2000','phone')
 ]);
}
loadAll();
</script>
</body></html>"""

def _mount_dashboard(core):
    global _MOUNTED
    if _MOUNTED:
        return False
    app=core.app

    @app.get("/v3/control-centre",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse(_html())

    _MOUNTED=True
    return True

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
            "lazy_activation":True,
            "dashboard_mounted":_MOUNTED,
            "legacy_dashboard_untouched":True,
        }

    @app.post("/api/v3/dashboard/activate")
    def dashboard_activate(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            mounted=_mount_dashboard(core)
            return {
                "version":MODULE_VERSION,
                "status":"ACTIVE",
                "mounted_now":mounted,
                "dashboard_mounted":True,
                "dashboard":"/v3/control-centre",
            }
        except Exception as exc:
            return {
                "version":MODULE_VERSION,
                "status":"ACTIVATION_ERROR",
                "message":str(exc),
            }
