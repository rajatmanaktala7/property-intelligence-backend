
from __future__ import annotations
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

MODULE_VERSION="3.7.2-BEAUTIFUL-LIVE-TEAM-DASHBOARD"

def _iso(v):
    return v.isoformat() if hasattr(v,"isoformat") else v

def _rows(rs):
    return [{k:_iso(v) for k,v in dict(r).items()} for r in rs]

def _table_exists(c,name):
    return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"),{"n":"public."+name}).scalar())

def register(app,engine,need_login):

    @app.get("/api/team-dashboard-v37/status")
    def status(req:Request):
        need_login(req)
        return {"version":MODULE_VERSION,"status":"OK","same_app":True,
                "dashboard":"/team-dashboard-live","startup_schema_ddl":False,
                "existing_data_only":True}

    @app.get("/api/team-dashboard-v37/whatsapp-health")
    def whatsapp_health(req:Request):
        need_login(req)
        try:
            import whatsapp_live_bridge as wb
            if wb.wa_engine is None:
                return {"status":"OFFLINE","configured":False,"accounts":0,"active_accounts":0,
                        "groups":0,"active_groups":0,"events":0,"latest_event":None}
            with wb.wa_engine.connect() as c:
                one=lambda sql:int(c.execute(text(sql)).scalar() or 0)
                latest=c.execute(text("""SELECT created_at,group_id,sender_name,sender_phone,
                    classification,status FROM wa_bridge_events ORDER BY id DESC LIMIT 1""")).mappings().first()
                return {"status":"LIVE","configured":True,
                        "accounts":one("SELECT COUNT(*) FROM wa_bridge_accounts"),
                        "active_accounts":one("SELECT COUNT(*) FROM wa_bridge_accounts WHERE active=TRUE"),
                        "groups":one("SELECT COUNT(*) FROM wa_bridge_groups"),
                        "active_groups":one("SELECT COUNT(*) FROM wa_bridge_groups WHERE active=TRUE AND auto_process=TRUE"),
                        "events":one("SELECT COUNT(*) FROM wa_bridge_events"),
                        "latest_event":{k:_iso(v) for k,v in dict(latest).items()} if latest else None,
                        "manage_url":"/whatsapp-live/sources","live_url":"/whatsapp-live"}
        except Exception as exc:
            return {"status":"ERROR","configured":False,"message":str(exc),
                    "manage_url":"/whatsapp-live/sources","live_url":"/whatsapp-live"}

    @app.get("/api/team-dashboard-v37/newspaper")
    def newspaper(req:Request,q:str="",limit:int=Query(100,ge=1,le=500)):
        need_login(req)
        try:
            with engine.connect() as c:
                if not _table_exists(c,"pi_newspaper_properties"):
                    return {"status":"NOT_READY","count":0,"rows":[]}
                where="";p={"lim":limit}
                if q.strip():
                    where="""WHERE COALESCE(locality,'') ILIKE :q OR COALESCE(configuration_details,'') ILIKE :q
                       OR COALESCE(agency_brand,'') ILIKE :q OR COALESCE(contact_person,'') ILIKE :q
                       OR COALESCE(phone_numbers,'') ILIKE :q OR COALESCE(notes,'') ILIKE :q"""
                    p["q"]="%"+q.strip()+"%"
                rs=c.execute(text(f"""SELECT id,lead_type,locality,area,configuration_details,price,
                    agency_brand,contact_person,phone_numbers,notes,completeness,verification,team_member,
                    created_at,updated_at FROM pi_newspaper_properties {where}
                    ORDER BY id DESC LIMIT :lim"""),p).mappings().all()
                return {"status":"OK","count":len(rs),"rows":_rows(rs),"upload_url":"/newspaper"}
        except Exception as exc:
            return {"status":"ERROR","message":str(exc),"count":0,"rows":[]}

    @app.get("/api/team-dashboard-v37/magazine")
    def magazine(req:Request,q:str="",limit:int=Query(100,ge=1,le=500)):
        need_login(req)
        try:
            with engine.connect() as c:
                if not _table_exists(c,"pi_magazine_master"):
                    return {"status":"NOT_READY","count":0,"rows":[]}
                where="";p={"lim":limit}
                if q.strip():
                    where="""WHERE COALESCE(source_id,'') ILIKE :q OR COALESCE(locality,'') ILIKE :q
                      OR COALESCE(plot_block,'') ILIKE :q OR COALESCE(configuration,'') ILIKE :q
                      OR COALESCE(contact_name_company,'') ILIKE :q OR COALESCE(valid_mobiles,'') ILIKE :q
                      OR COALESCE(valid_landlines,'') ILIKE :q OR COALESCE(original_raw_text,'') ILIKE :q"""
                    p["q"]="%"+q.strip()+"%"
                rs=c.execute(text(f"""SELECT source_id,record_status,match_eligible,category,listing_type,
                  locality,plot_block,configuration,area,area_unit,floor,price,status_remarks,
                  contact_name_company,valid_mobiles,valid_landlines,valid_contact_count,
                  quality_issues,import_batch,updated_at
                  FROM pi_magazine_master {where}
                  ORDER BY updated_at DESC NULLS LAST,source_id DESC LIMIT :lim"""),p).mappings().all()
                return {"status":"OK","count":len(rs),"rows":_rows(rs),
                        "archive_url":"/magazine-master-import"}
        except Exception as exc:
            return {"status":"ERROR","message":str(exc),"count":0,"rows":[]}

    @app.get("/api/team-dashboard-v37/hospitality")
    def hospitality(req:Request,q:str="",category:str="ALL",limit:int=Query(100,ge=1,le=500)):
        need_login(req)
        try:
            with engine.connect() as c:
                if not _table_exists(c,"ai_hospitality_entity"):
                    return {"status":"NOT_READY","count":0,"rows":[]}
                wh=[];p={"lim":limit}
                if q.strip():
                    wh.append("""(COALESCE(business_name,'') ILIKE :q OR COALESCE(location,'') ILIKE :q
                      OR COALESCE(city,'') ILIKE :q OR COALESCE(contact_name,'') ILIKE :q
                      OR COALESCE(contact_phone,'') ILIKE :q OR COALESCE(whatsapp_phone,'') ILIKE :q
                      OR COALESCE(email,'') ILIKE :q OR COALESCE(website,'') ILIKE :q)""")
                    p["q"]="%"+q.strip()+"%"
                if category.upper()!="ALL":
                    wh.append("category=:cat");p["cat"]=category.upper()
                where="WHERE "+" AND ".join(wh) if wh else ""
                rs=c.execute(text(f"""SELECT hospitality_id,business_name,category,location,city,
                  contact_name,contact_phone,whatsapp_phone,email,website,verification_status,
                  outreach_status,assigned_to,notes,active,updated_at
                  FROM ai_hospitality_entity {where}
                  ORDER BY updated_at DESC NULLS LAST,hospitality_id DESC LIMIT :lim"""),p).mappings().all()
                return {"status":"OK","count":len(rs),"rows":_rows(rs)}
        except Exception as exc:
            return {"status":"ERROR","message":str(exc),"count":0,"rows":[]}

    @app.get("/api/team-dashboard-v37/retail")
    def retail(req:Request,q:str="",limit:int=Query(100,ge=1,le=500)):
        need_login(req)
        try:
            with engine.connect() as c:
                if not _table_exists(c,"ai_retail_expansion_signal"):
                    return {"status":"NOT_READY","count":0,"rows":[]}
                where="";p={"lim":limit}
                if q.strip():
                    where="""WHERE COALESCE(company_name,'') ILIKE :q OR COALESCE(headline,'') ILIKE :q
                      OR COALESCE(category,'') ILIKE :q OR COALESCE(location_signal,'') ILIKE :q
                      OR COALESCE(evidence_text,'') ILIKE :q"""
                    p["q"]="%"+q.strip()+"%"
                rs=c.execute(text(f"""SELECT signal_id,company_name,category,headline,source_name,
                  source_url,published_at,intent_score,intent_status,location_signal,outlet_target,
                  first_seen_at,last_seen_at FROM ai_retail_expansion_signal {where}
                  ORDER BY signal_id DESC LIMIT :lim"""),p).mappings().all()
                return {"status":"OK","count":len(rs),"rows":_rows(rs)}
        except Exception as exc:
            return {"status":"ERROR","message":str(exc),"count":0,"rows":[]}

    @app.get("/api/team-dashboard-v37/exact-databases")
    def exact_databases(req:Request):
        need_login(req)
        return {
            "version":MODULE_VERSION,
            "requirements":{
                "label":"Delhi NCR Requirements Centre",
                "url":"/requirements-center-v176?division=DELHI_NCR",
                "add_url":"/manual-requirement-final?division=DELHI_NCR",
                "matcher_url":"/matcher-final?division=DELHI_NCR",
                "mode":"EXACT_EXISTING_DATABASE"
            },
            "manual_properties":{
                "label":"Manual Property Database",
                "url":"/manual-property-database",
                "add_delhi_ncr":"/manual-property-final-exec?division=DELHI_NCR",
                "add_goa":"/manual-property-final-exec?division=GOA",
                "supports":["FULL_DETAILS","PHOTOS","VIDEOS","BROCHURES","VIEW","EDIT"],
                "mode":"EXACT_EXISTING_DATABASE"
            }
        }

    @app.get("/team-dashboard-live",response_class=HTMLResponse)
    def page(req:Request):
        need_login(req)
        return HTMLResponse(DASHBOARD_HTML)

    @app.middleware("http")
    async def dashboard_takeover(request,call_next):
        if request.url.path in {"/workspace","/final-dashboard-v11","/final-dashboard-v13"}:
            return RedirectResponse("/team-dashboard-live",status_code=307)
        return await call_next(request)

DASHBOARD_HTML=r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Deal Intelligence OS</title>
<style>
:root{
  --nav:#10223f;
  --nav2:#17345d;
  --bg:#f5f7fb;
  --card:#ffffff;
  --line:#e6eaf0;
  --text:#1d2a3a;
  --muted:#6c7a8a;
  --blue:#2f6fed;
  --blue2:#4f83f1;
  --green:#138a5b;
  --green2:#eaf8f1;
  --purple:#7c4dff;
  --purple2:#efeaff;
  --amber:#b7791f;
  --amber2:#fff7e6;
  --red:#c63d3d;
  --shadow:0 8px 24px rgba(31,47,70,.08);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;
  background:linear-gradient(180deg,#f8faff 0%,#f4f7fb 100%);
  font-family:Inter,Segoe UI,Arial,sans-serif;
  color:var(--text);
}
header{
  background:linear-gradient(135deg,var(--nav) 0%,var(--nav2) 100%);
  color:#fff;
  padding:18px 24px;
  display:flex;
  justify-content:space-between;
  gap:18px;
  align-items:center;
  flex-wrap:wrap;
  box-shadow:0 6px 20px rgba(16,34,63,.18);
}
header b{
  font-size:22px;
  letter-spacing:.2px;
}
.wrap{
  max-width:1650px;
  margin:auto;
  padding:20px;
}
.corner{
  display:grid;
  grid-template-columns:repeat(5,minmax(118px,1fr));
  gap:8px;
}
.kpi{
  background:rgba(255,255,255,.96);
  color:var(--text);
  border-radius:12px;
  padding:10px 12px;
  border:1px solid rgba(255,255,255,.35);
  box-shadow:0 5px 16px rgba(0,0,0,.08);
  min-width:120px;
}
.kpi span{
  display:block;
  font-size:10px;
  color:var(--muted);
  text-transform:uppercase;
  letter-spacing:.6px;
  font-weight:700;
}
.kpi strong{
  display:block;
  font-size:22px;
  margin-top:2px;
  color:#16315a;
}
.tabs{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  margin:16px 0 18px;
  padding:10px;
  background:#fff;
  border:1px solid var(--line);
  border-radius:14px;
  box-shadow:var(--shadow);
}
.tabs button,.btn{
  padding:10px 13px;
  border:1px solid transparent;
  border-radius:10px;
  background:#f7f9fc;
  font-weight:700;
  color:var(--text);
  cursor:pointer;
  text-decoration:none;
  transition:.18s ease;
}
.tabs button:hover,.btn:hover{
  transform:translateY(-1px);
  box-shadow:0 5px 12px rgba(31,47,70,.08);
}
.tabs button.active{
  background:linear-gradient(135deg,var(--blue),var(--blue2));
  color:white;
  box-shadow:0 6px 14px rgba(47,111,237,.24);
}
.btn.primary{
  background:linear-gradient(135deg,var(--blue),var(--blue2));
  color:white;
}
.btn.green{
  background:linear-gradient(135deg,#0f8a59,#18a36c);
  color:white;
}
.btn.purple{
  background:linear-gradient(135deg,#6f43e8,#8d63ff);
  color:white;
}
.card{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:16px;
  padding:18px;
  margin-bottom:15px;
  box-shadow:var(--shadow);
}
.card h2,.card h3{
  margin-top:0;
  color:#16315a;
}
.toolbar{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  align-items:center;
  margin:10px 0 12px;
}
input,select{
  padding:10px 11px;
  border:1px solid #d3dbe6;
  border-radius:9px;
  background:#fff;
  color:var(--text);
  min-height:40px;
  outline:none;
}
input:focus,select:focus{
  border-color:#7ba1f4;
  box-shadow:0 0 0 3px rgba(47,111,237,.10);
}
.tablewrap{
  overflow:auto;
  max-height:620px;
  border:1px solid var(--line);
  border-radius:12px;
  background:#fff;
}
table{
  width:100%;
  border-collapse:collapse;
  min-width:1050px;
}
th,td{
  padding:10px 9px;
  border-bottom:1px solid #eef1f5;
  text-align:left;
  font-size:12px;
  vertical-align:top;
}
th{
  background:#f2f6fb;
  color:#344860;
  position:sticky;
  top:0;
  z-index:2;
  text-transform:uppercase;
  letter-spacing:.35px;
  font-size:10px;
}
tbody tr:hover{
  background:#f9fbff;
}
.hidden{display:none}
.small{
  font-size:11px;
  color:var(--muted);
}
.split{
  display:grid;
  grid-template-columns:1.08fr .92fr;
  gap:14px;
}
.message{
  background:linear-gradient(180deg,#fffaf0 0%,#fff6e7 100%);
  border:1px solid #f2dfb4;
  color:#7b5314;
  padding:11px 12px;
  border-radius:10px;
}
.flow{
  display:flex;
  align-items:stretch;
  gap:10px;
  overflow:auto;
  padding:12px 2px 6px;
}
.box{
  min-width:185px;
  border:2px solid #dce3ec;
  border-radius:14px;
  padding:15px;
  background:#fff;
  box-shadow:0 4px 12px rgba(31,47,70,.05);
  position:relative;
}
.box b{
  display:block;
  margin-bottom:5px;
  color:#17345d;
}
.box.done{
  border-color:#2bb673;
  background:linear-gradient(180deg,#f2fcf7 0%,#eaf8f1 100%);
}
.box.done:before{
  content:"✓";
  position:absolute;
  top:8px;
  right:10px;
  color:#148954;
  font-weight:800;
}
.box.active{
  border-color:var(--blue);
  background:linear-gradient(180deg,#f4f8ff 0%,#eaf1ff 100%);
  box-shadow:0 6px 18px rgba(47,111,237,.13);
}
.box.active:before{
  content:"LIVE";
  position:absolute;
  top:8px;
  right:9px;
  background:var(--blue);
  color:#fff;
  font-size:9px;
  padding:3px 6px;
  border-radius:999px;
  font-weight:800;
}
.box.wait{
  border-color:#e0a13b;
  background:linear-gradient(180deg,#fffaf0 0%,#fff3dc 100%);
}
.box.wait:before{
  content:"VERIFY";
  position:absolute;
  top:8px;
  right:9px;
  background:#d88b16;
  color:#fff;
  font-size:9px;
  padding:3px 6px;
  border-radius:999px;
  font-weight:800;
}
.arrow{
  display:flex;
  align-items:center;
  font-size:27px;
  color:#91a1b5;
  font-weight:700;
}
.reqtag{
  font-size:10px;
  margin-top:8px;
  color:#4b5f76;
  word-break:break-word;
}
.embedwrap{
  background:#fff;
  border:1px solid var(--line);
  border-radius:16px;
  overflow:hidden;
  box-shadow:var(--shadow);
}
.embedhead{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:14px 16px;
  border-bottom:1px solid var(--line);
  background:linear-gradient(180deg,#fff 0%,#f9fbfd 100%);
  flex-wrap:wrap;
}
.embedhead h2{
  margin:0;
  color:#16315a;
}
.embedframe{
  display:block;
  width:100%;
  height:780px;
  border:0;
  background:#fff;
}
.status-pill{
  display:inline-block;
  padding:4px 8px;
  border-radius:999px;
  font-size:10px;
  font-weight:800;
}
@media(max-width:1050px){
  .corner{grid-template-columns:repeat(3,1fr)}
  .split{grid-template-columns:1fr}
}
@media(max-width:680px){
  header{align-items:flex-start}
  .corner{grid-template-columns:repeat(2,1fr);width:100%}
  .wrap{padding:12px}
  .tabs{padding:8px}
  .box{min-width:165px}
}
</style></head><body>
<header><div><b>AI Deal Intelligence OS</b><div class="small" style="color:#cbd5e1">FINAL EXECUTION DASHBOARD · Live Team Database View</div></div>
<div class="corner"><div class="kpi"><span>Dashboard</span><strong>LIVE</strong></div><div class="kpi"><span>WhatsApp Records</span><strong id="kw">—</strong></div><div class="kpi"><span>Newspaper Records</span><strong id="kn">—</strong></div><div class="kpi"><span>Hospitality</span><strong id="kh">—</strong></div><div class="kpi"><span>Retail Signals</span><strong id="kr">—</strong></div></div></header>
<div class="wrap"><div class="tabs">
<button class="active" onclick="tab('home',this)">Dashboard</button><button onclick="tab('requirements',this)">Requirements Centre</button><button onclick="tab('manualexact',this)">Manual Property Database</button><button onclick="tab('whatsapp',this)">WhatsApp Live</button><button onclick="tab('newspaper',this)">Newspaper Live</button><button onclick="tab('magazine',this)">Magazine Live</button><button onclick="tab('hospitality',this)">Hospitality Database</button><button onclick="tab('pipeline',this)">Live Property Pipeline</button>
<a class="btn" href="/matcher-final?division=DELHI_NCR">Matcher</a><a class="btn" href="/property-discovery">Property Search</a></div>

<section id="home"><div class="split">
<div class="card"><h2>Team Daily Operations</h2><div class="toolbar"><a class="btn green" href="/manual-property-database">Manual Property Database</a><a class="btn primary" href="/manual-property-final-exec?division=DELHI_NCR">Add Delhi NCR Property</a><a class="btn" href="/manual-property-final-exec?division=GOA">Add Goa Property</a><a class="btn purple" href="/whatsapp-live">Open WhatsApp Live</a><a class="btn" href="/newspaper">Upload Newspaper Picture</a></div></div>
<div class="card"><h2>WhatsApp Connection</h2><div id="wastatus">Checking…</div><div class="toolbar"><a class="btn purple" href="/whatsapp-live">Open Live Feed</a><a class="btn green" href="/whatsapp-live/sources">Add / Manage Numbers & Groups</a></div><div class="small">Add the mobile account first, then add/activate each WhatsApp group under that number.</div></div></div>
<div class="card"><h2>AI Bots</h2><div class="toolbar"><button class="btn purple" onclick="runBot('/api/v4/hospitality-bot/start','Hospitality')">▶ Run Hospitality Bot</button><a class="btn" href="/hospitality-results-final">Hospitality Results</a><button class="btn purple" onclick="runBot('/api/v4/retail-bot/start','Retail')">▶ Run Retail Bot</button><a class="btn" href="/bot-reliability">Bot Reliability</a></div><div id="botmsg" class="message">Ready.</div></div></section>


<section id="requirements" class="hidden">
<div class="embedwrap">
  <div class="embedhead">
    <div>
      <h2 style="margin:0">Delhi NCR Requirements Centre</h2>
      <div class="small">Exact existing requirements database · same codes · same verification status · same matcher workflow</div>
    </div>
    <div class="toolbar" style="margin:0">
      <a class="btn green" target="_top" href="/manual-requirement-final?division=DELHI_NCR">+ Add / Confirm Requirement</a>
      <a class="btn primary" target="_top" href="/matcher-final?division=DELHI_NCR">Matcher</a>
      <a class="btn" target="_blank" href="/requirements-center-v176?division=DELHI_NCR">Open Full Page</a>
    </div>
  </div>
  <iframe class="embedframe" src="/requirements-center-v176?division=DELHI_NCR" title="Delhi NCR Requirements Centre"></iframe>
</div>
</section>

<section id="manualexact" class="hidden">
<div class="embedwrap">
  <div class="embedhead">
    <div>
      <h2 style="margin:0">Manual Property Database</h2>
      <div class="small">Exact existing manual inventory database · property details · photos · videos · brochures · View / Edit</div>
    </div>
    <div class="toolbar" style="margin:0">
      <a class="btn green" target="_top" href="/manual-property-final-exec?division=DELHI_NCR">+ Add Delhi NCR Property</a>
      <a class="btn" target="_top" href="/manual-property-final-exec?division=GOA">+ Add Goa Property</a>
      <a class="btn primary" target="_blank" href="/manual-property-database">Open Full Database</a>
    </div>
  </div>
  <iframe class="embedframe" src="/manual-property-database" title="Manual Property Database"></iframe>
</div>
</section>

<section id="whatsapp" class="hidden"><div class="card"><h2>WhatsApp Live Status</h2><div id="wadetail">Checking…</div><div class="toolbar"><a class="btn purple" href="/whatsapp-live">Open Full WhatsApp Dashboard</a><a class="btn green" href="/whatsapp-live/sources">Add / Manage Numbers & Groups</a></div></div></section>

<section id="newspaper" class="hidden"><div class="card"><h2>Newspaper Live Database</h2><div class="message">Picture upload only. Manual newspaper entry has been removed from this dashboard.</div><div class="toolbar"><input id="nq" placeholder="Search newspaper database"><button class="btn primary" onclick="newspaper()">Search</button><a class="btn green" href="/newspaper">📷 Upload Newspaper Picture</a></div><div id="ncount" class="small"></div><div class="tablewrap"><table><thead><tr><th>ID</th><th>Type</th><th>Locality</th><th>Area</th><th>Configuration</th><th>Price</th><th>Agency</th><th>Contact</th><th>Phone</th><th>Verification</th><th>Team</th></tr></thead><tbody id="nrows"></tbody></table></div></div></section>

<section id="magazine" class="hidden"><div class="card"><h2>Magazine Live Database</h2><div class="toolbar"><input id="mq" placeholder="Search magazine database"><button class="btn primary" onclick="magazine()">Search</button><a class="btn green" href="/magazine-master-import">Magazine Import / Activation</a><a class="btn" href="/capture-intelligence">Upload Magazine / PDF</a></div><div id="mcount" class="small"></div><div class="tablewrap"><table><thead><tr><th>Source</th><th>Status</th><th>Category</th><th>Listing</th><th>Locality</th><th>Plot</th><th>Configuration</th><th>Area</th><th>Floor</th><th>Price</th><th>Name/Company</th><th>Mobile</th><th>Landline</th><th>Quality</th></tr></thead><tbody id="mrows"></tbody></table></div></div></section>

<section id="hospitality" class="hidden"><div class="card"><h2>Hospitality Database</h2><div class="toolbar"><input id="hq" placeholder="Search name, address, phone, website"><select id="hcat"><option>ALL</option><option>RESTAURANT</option><option>CAFE</option><option>LOUNGE</option><option>CLUB</option><option>BANQUET</option><option>HOTEL</option><option>GUEST_HOUSE</option></select><button class="btn primary" onclick="hospitality()">Search</button><a class="btn green" href="/hospitality-enrichment">Find Missing Contacts</a></div><div id="hcount" class="small"></div><div class="tablewrap"><table><thead><tr><th>ID</th><th>Business</th><th>Category</th><th>Location</th><th>City</th><th>Contact</th><th>Phone</th><th>WhatsApp</th><th>Email</th><th>Website</th><th>Verification</th><th>Assigned</th></tr></thead><tbody id="hrows"></tbody></table></div></div></section>

<section id="manual" class="hidden"><div class="card"><h2>Manual Property Database</h2><div class="toolbar"><input id="pq" placeholder="Search property/location/contact"><button class="btn primary" onclick="manual()">Search</button><a class="btn green" href="/manual-property-final-exec?division=DELHI_NCR">+ Add Property</a><a class="btn" href="/manual-property-database">Open Full Database</a></div><div id="pcount" class="small"></div><div class="tablewrap"><table><thead><tr><th>No.</th><th>Property</th><th>Code</th><th>Location</th><th>Area</th><th>Rent</th><th>Contact</th><th>Phone</th><th>Verification</th><th>Media</th><th>Actions</th></tr></thead><tbody id="prows"></tbody></table></div></div></section>

<section id="pipeline" class="hidden"><div class="card"><h2>Live Property Pipeline Flowchart</h2><div class="toolbar"><input id="reqcode" placeholder="Requirement code"><button class="btn primary" onclick="pipeline()">Refresh Flow</button><button class="btn purple" onclick="runPipeline()">Run Selected Requirement</button></div><div id="flowtitle"></div>
<div class="flow"><div id="f26" class="box"><b>V2.6 · Team Action</b><div>Requirement/action created</div><div class="reqtag"></div></div><div class="arrow">→</div><div id="f27" class="box"><b>V2.7 · Existing Inventory</b><div>Search internal inventory</div><div class="reqtag"></div></div><div class="arrow">→</div><div id="f28" class="box"><b>V2.8 · External Discovery</b><div>Search external sources</div><div class="reqtag"></div></div><div class="arrow">→</div><div id="f29" class="box"><b>V2.9A · Entity Splitter</b><div>Split pages into properties</div><div class="reqtag"></div></div><div class="arrow">→</div><div id="f295" class="box"><b>V2.9.5 · Verification</b><div>Human verification gate</div><div class="reqtag"></div></div></div><div id="flowmsg" class="message">No auto-share. Human verification remains mandatory.</div></div></section>
</div>
<script>
const E=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));async function J(u,o={}){try{let r=await fetch(u,{credentials:'include',...o});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}};return{ok:r.ok,d}}catch(e){return{ok:false,d:{error:String(e)}}}}
function tab(id,b){document.querySelectorAll('section').forEach(x=>x.classList.add('hidden'));document.getElementById(id).classList.remove('hidden');document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));if(b)b.classList.add('active');if(id==='whatsapp')wa();if(id==='newspaper')newspaper();if(id==='magazine')magazine();if(id==='hospitality')hospitality();if(id==='pipeline')pipeline()}
async function wa(){let x=await J('/api/team-dashboard-v37/whatsapp-health');let d=x.d;kw.textContent=d.events??0;let h=`<b>${E(d.status)}</b> · Active numbers ${E(d.active_accounts||0)}/${E(d.accounts||0)} · Active groups ${E(d.active_groups||0)}/${E(d.groups||0)} · Events ${E(d.events||0)}`;wadetail.innerHTML=h;wastatus.innerHTML=h}
async function newspaper(){let x=await J('/api/team-dashboard-v37/newspaper?q='+encodeURIComponent(nq.value||'')+'&limit=200');kn.textContent=x.d.count??0;ncount.textContent=(x.d.count??0)+' shown';nrows.innerHTML=(x.d.rows||[]).map(r=>`<tr><td>${E(r.id)}</td><td>${E(r.lead_type)}</td><td>${E(r.locality)}</td><td>${E(r.area)}</td><td>${E(r.configuration_details)}</td><td>${E(r.price)}</td><td>${E(r.agency_brand)}</td><td>${E(r.contact_person)}</td><td>${E(r.phone_numbers)}</td><td>${E(r.verification)}</td><td>${E(r.team_member)}</td></tr>`).join('')||'<tr><td colspan=11>No records.</td></tr>'}
async function magazine(){let x=await J('/api/team-dashboard-v37/magazine?q='+encodeURIComponent(mq.value||'')+'&limit=200');mcount.textContent=(x.d.count??0)+' shown';mrows.innerHTML=(x.d.rows||[]).map(r=>`<tr><td>${E(r.source_id)}</td><td>${E(r.record_status)}</td><td>${E(r.category)}</td><td>${E(r.listing_type)}</td><td>${E(r.locality)}</td><td>${E(r.plot_block)}</td><td>${E(r.configuration)}</td><td>${E(r.area)} ${E(r.area_unit)}</td><td>${E(r.floor)}</td><td>${E(r.price)}</td><td>${E(r.contact_name_company)}</td><td>${E(r.valid_mobiles)}</td><td>${E(r.valid_landlines)}</td><td>${E(r.quality_issues)}</td></tr>`).join('')||'<tr><td colspan=14>No records.</td></tr>'}
async function hospitality(){let x=await J('/api/team-dashboard-v37/hospitality?q='+encodeURIComponent(hq.value||'')+'&category='+encodeURIComponent(hcat.value)+'&limit=200');kh.textContent=x.d.count??0;hcount.textContent=(x.d.count??0)+' shown';hrows.innerHTML=(x.d.rows||[]).map(r=>`<tr><td>${E(r.hospitality_id)}</td><td><b>${E(r.business_name)}</b></td><td>${E(r.category)}</td><td>${E(r.location)}</td><td>${E(r.city)}</td><td>${E(r.contact_name)}</td><td><b>${E(r.contact_phone)}</b></td><td>${E(r.whatsapp_phone)}</td><td>${E(r.email)}</td><td>${r.website?`<a target=_blank href="${E(r.website)}">Website</a>`:''}</td><td>${E(r.verification_status)}</td><td>${E(r.assigned_to)}</td></tr>`).join('')||'<tr><td colspan=12>No records.</td></tr>'}
async function retail(){let x=await J('/api/team-dashboard-v37/retail?limit=500');kr.textContent=x.d.count??0}
async function manual(){let x=await J('/api/v17-7/manual-properties?division=ALL&source=MANUAL&verified=ALL&q='+encodeURIComponent(pq.value||''));let a=x.d.rows||[];pcount.textContent=a.length+' shown';prows.innerHTML=a.map((r,i)=>`<tr><td><b>${i+1}</b></td><td><b>${E(r.property_name||r.property_code)}</b></td><td>${E(r.property_code)}</td><td>${E(r.location)}</td><td>${E(r.area_sqft)} sq ft</td><td>${r.rent_amount==null?'':'₹'+E(r.rent_amount)}</td><td>${E(r.owner_broker_name)}</td><td><b>${E(r.contact_number)}</b></td><td>${E(r.verification_status)}</td><td>Photos ${E(r.image_count||0)} · Videos ${E(r.video_count||0)} · Brochure ${E(r.brochure_count||0)}</td><td><a href="/property-detail-final/${encodeURIComponent(r.property_code)}">View</a> · <a href="/edit-property/${encodeURIComponent(r.property_code)}">Edit</a></td></tr>`).join('')||'<tr><td colspan=11>No properties.</td></tr>'}
function stage(s){s=String(s||'').toUpperCase();if(s.includes('2.9.5')||s.includes('VERIFY'))return 5;if(s.includes('2.9A')||s.includes('SPLIT'))return 4;if(s.includes('2.8')||s.includes('EXTERNAL'))return 3;if(s.includes('2.7')||s.includes('INVENTORY'))return 2;if(s.includes('2.6')||s.includes('TEAM'))return 1;return 0}
async function pipeline(){let x=await J('/api/v2/intelligence/v30/runs');let a=x.d.runs||x.d.rows||[];let c=(reqcode.value||'').trim();let r=c?a.find(z=>z.requirement_code===c):a[0];let ids=['f26','f27','f28','f29','f295'];ids.forEach(id=>{let e=document.getElementById(id);e.className='box';e.querySelector('.reqtag').textContent=''});if(!r){flowtitle.textContent='No run found.';return}let n=stage(r.current_step||r.next_step);ids.forEach((id,i)=>{let e=document.getElementById(id);if(i+1<n)e.classList.add('done');if(i+1===n)e.classList.add(r.requires_human_review?'wait':'active');e.querySelector('.reqtag').textContent=(i+1===n?'CURRENT: ':'')+r.requirement_code});flowtitle.textContent=`${r.requirement_code} · ${r.run_status||''} · Current: ${r.current_step||''} · Next: ${r.next_step||''}`;flowmsg.textContent=r.requires_human_review?'WAITING FOR HUMAN VERIFICATION':'Highlighted box is the current stage.'}
async function runPipeline(){let c=(reqcode.value||'').trim();if(!c){alert('Enter requirement code');return}flowmsg.textContent='Running…';let x=await J('/api/v2/intelligence/v30/run/'+encodeURIComponent(c),{method:'POST'});flowmsg.textContent=JSON.stringify(x.d);setTimeout(pipeline,500)}
async function runBot(url,name){botmsg.textContent='Running '+name+'…';let x=await J(url,{method:'POST'});botmsg.textContent=name+': '+JSON.stringify(x.d);setTimeout(()=>{hospitality();retail()},500)}
wa();newspaper();hospitality();retail();
</script></body></html>"""
