
from __future__ import annotations
import os,re,math
from datetime import datetime
from fastapi import APIRouter, Request, Body, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text, inspect, create_engine

VERSION="3.8.0-SOURCE-AWARE-MATCHER-WHATSAPP-CENTRE"

SOURCE_COLORS={
    "MANUAL":"#2f6fed",
    "WHATSAPP":"#16845b",
    "MAGAZINE":"#7c4dff",
    "NEWSPAPER":"#d98200",
}

def _db_url(url):
    u=(url or "").strip()
    if u.startswith("postgres://"): return u.replace("postgres://","postgresql+psycopg://",1)
    if u.startswith("postgresql://"): return u.replace("postgresql://","postgresql+psycopg://",1)
    return u

def _wa_engine():
    u=(os.getenv("WHATSAPP_DATABASE_URL") or "").strip()
    return create_engine(_db_url(u),pool_pre_ping=True,pool_recycle=300) if u else None

def _norm(s):
    return re.sub(r"[^A-Z0-9 ]+"," ",str(s or "").upper()).strip()

def _num(v):
    if v in (None,""): return None
    try: return float(v)
    except: pass
    s=str(v).replace(",","")
    m=re.search(r"(\d+(?:\.\d+)?)",s)
    return float(m.group(1)) if m else None

def _money(v):
    if v in (None,""): return None
    if isinstance(v,(int,float)): return float(v)
    s=str(v).lower().replace(",","").replace("₹","")
    m=re.search(r"(\d+(?:\.\d+)?)",s)
    if not m:return None
    n=float(m.group(1))
    if "crore" in s or " cr" in s: n*=10000000
    elif "lac" in s or "lakh" in s: n*=100000
    elif "k" in s: n*=1000
    return n

def _first(d,*keys):
    low={str(k).lower():v for k,v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in (None,""):
            return low[k.lower()]
    return None

def _serialize(rows):
    out=[]
    for r in rows:
        d=dict(r)
        for k,v in list(d.items()):
            if hasattr(v,"isoformat"): d[k]=v.isoformat()
        out.append(d)
    return out

def _table_exists(engine,name):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"),{"n":"public."+name}).scalar())
    except:return False

def _read_table(engine,name,limit=5000):
    if not _table_exists(engine,name): return []
    try:
        with engine.connect() as c:
            return _serialize(c.execute(text(f'SELECT * FROM "{name}" LIMIT :lim'),{"lim":limit}).mappings().all())
    except Exception:
        return []

def _manual_rows(engine,limit=5000):
    # Prefer the unified index because it already preserves source provenance.
    if _table_exists(engine,"ai_property_match_index"):
        try:
            with engine.connect() as c:
                rows=c.execute(text("""SELECT * FROM ai_property_match_index
                    WHERE UPPER(COALESCE(source_type,'')) IN ('MANUAL','MANUAL_SURVEY')
                    ORDER BY updated_at DESC NULLS LAST LIMIT :lim"""),{"lim":limit}).mappings().all()
            if rows:return _serialize(rows)
        except Exception:pass
    for t in ["ai_manual_property_final","pi_manual_property_final","manual_property_final","properties"]:
        rows=_read_table(engine,t,limit)
        if rows:return rows
    return []

def _newspaper_rows(engine,limit=5000):
    return _read_table(engine,"pi_newspaper_properties",limit)

def _magazine_rows(engine,limit=5000):
    return _read_table(engine,"pi_magazine_master",limit)

def _wa_properties(limit=5000):
    w=_wa_engine()
    if w is None:return []
    try:
        with w.connect() as c:
            return _serialize(c.execute(text("""SELECT p.*,s.group_name source_group
                FROM wa_properties p
                LEFT JOIN wa_sources s ON s.source_id=p.source_id
                WHERE COALESCE(p.record_status,'ACTIVE')='ACTIVE'
                ORDER BY p.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all())
    except:return []

def _normalize_property(row,source):
    d=dict(row)
    if source=="WHATSAPP":
        return {
            "source":"WHATSAPP",
            "source_name":_first(d,"source_group","group_name","source_name") or "WhatsApp",
            "source_record_id":_first(d,"wa_property_id","id"),
            "property_name":_first(d,"property_name","location","locality") or "WhatsApp Property",
            "location":_first(d,"location","locality","city"),
            "property_type":_first(d,"property_type"),
            "transaction":_first(d,"transaction_type"),
            "area_min":_num(_first(d,"available_area_sqft","area_sqft")),
            "area_max":_num(_first(d,"available_area_sqft","area_sqft")),
            "rent":_money(_first(d,"rent_inr")),
            "contact_name":_first(d,"owner_name","broker_name","sender_name"),
            "contact_phone":_first(d,"owner_phone","broker_phone","sender_phone"),
            "verification":_first(d,"verification_status") or "UNVERIFIED",
            "captured_at":_first(d,"first_seen","created_at","last_seen"),
            "raw":d
        }
    if source=="NEWSPAPER":
        return {
            "source":"NEWSPAPER","source_name":_first(d,"source") or "Newspaper",
            "source_record_id":_first(d,"record_id","id"),
            "property_name":_first(d,"locality","agency_brand") or "Newspaper Property",
            "location":_first(d,"locality"),
            "property_type":_first(d,"lead_type"),
            "transaction":_first(d,"lead_type"),
            "area_min":_num(_first(d,"area")),
            "area_max":_num(_first(d,"area")),
            "rent":_money(_first(d,"price")),
            "contact_name":_first(d,"contact_person","agency_brand"),
            "contact_phone":_first(d,"phone_numbers"),
            "verification":_first(d,"verification") or "UNVERIFIED",
            "captured_at":_first(d,"date_captured","created_at","updated_at"),
            "raw":d
        }
    if source=="MAGAZINE":
        return {
            "source":"MAGAZINE","source_name":"Magazine Master",
            "source_record_id":_first(d,"source_id","id"),
            "property_name":_first(d,"locality","plot_block","configuration") or "Magazine Property",
            "location":_first(d,"locality"),
            "property_type":_first(d,"category","configuration"),
            "transaction":_first(d,"listing_type"),
            "area_min":_num(_first(d,"area")),
            "area_max":_num(_first(d,"area")),
            "rent":_money(_first(d,"price")),
            "contact_name":_first(d,"contact_name_company"),
            "contact_phone":_first(d,"valid_mobiles","valid_landlines"),
            "verification":_first(d,"record_status") or "UNVERIFIED",
            "captured_at":_first(d,"updated_at"),
            "raw":d
        }
    return {
        "source":"MANUAL",
        "source_name":_first(d,"source_name","source") or "Manual Property Database",
        "source_record_id":_first(d,"property_code","source_record_id","id"),
        "property_name":_first(d,"property_name","property_code","location") or "Manual Property",
        "location":_first(d,"location","locality","city"),
        "property_type":_first(d,"property_type","type"),
        "transaction":_first(d,"transaction_type","rent_sale","listing_type"),
        "area_min":_num(_first(d,"area_min_sqft","minimum_area","area_sqft","available_area","area")),
        "area_max":_num(_first(d,"area_max_sqft","maximum_area","area_sqft","available_area","area")),
        "rent":_money(_first(d,"monthly_rent","rent_amount","rent_inr","rent")),
        "contact_name":_first(d,"owner_broker_name","owner_name","broker_name","contact_name"),
        "contact_phone":_first(d,"contact_number","owner_phone","broker_phone","phone"),
        "verification":_first(d,"verification_status","verified","verification") or "UNVERIFIED",
        "captured_at":_first(d,"updated_at","created_at","date_captured"),
        "raw":d
    }

def _score(req,p):
    score=0; reasons=[]
    rloc=_norm(req.get("location")); ploc=_norm(p.get("location"))
    if rloc:
        if rloc==ploc or rloc in ploc or ploc in rloc:
            score+=40; reasons.append("Location aligned")
        else:
            rt=set(rloc.split()); pt=set(ploc.split())
            overlap=len(rt & pt)/max(1,len(rt))
            if overlap>=.5: score+=28;reasons.append("Partial location match")
            elif overlap>0: score+=12;reasons.append("Weak location overlap")
    else:
        score+=20;reasons.append("No location restriction")

    amin=_num(req.get("area_min"));amax=_num(req.get("area_max"))
    pmin=_num(p.get("area_min"));pmax=_num(p.get("area_max"))
    if amin or amax:
        lo=amin or 0; hi=amax or amin or 10**9
        plo=pmin or pmax; phi=pmax or pmin
        if plo is not None:
            if phi>=lo and plo<=hi:
                score+=25;reasons.append("Area overlaps requirement")
            else:
                gap=min(abs(plo-hi),abs(phi-lo))/max(1,hi)
                if gap<=.15:score+=12;reasons.append("Area near requirement")
    else: score+=12

    rtype=_norm(req.get("property_type"));ptype=_norm(p.get("property_type"))
    if rtype:
        if rtype in ptype or ptype in rtype:
            score+=15;reasons.append("Property type aligned")
        elif any(x in ptype for x in ["COMMERCIAL","SHOP","SHOWROOM","OFFICE","RESTAURANT"]):
            score+=7;reasons.append("Broad commercial suitability")
    else:score+=8

    rtxn=_norm(req.get("transaction"));ptxn=_norm(p.get("transaction"))
    if rtxn:
        if rtxn in ptxn or ptxn in rtxn or ("LEASE" in rtxn and "RENT" in ptxn) or ("RENT" in rtxn and "LEASE" in ptxn):
            score+=10;reasons.append("Transaction aligned")
    else:score+=5

    budget=_money(req.get("budget_max"))
    rent=_money(p.get("rent"))
    if budget and rent:
        if rent<=budget:score+=10;reasons.append("Within budget")
        elif rent<=budget*1.15:score+=5;reasons.append("Slightly above budget")
    elif not budget:score+=5

    return min(100,round(score,1)),reasons

def register(core):
    app=core.app; engine=core.engine; need_login=core.need_login; page_role_or_redirect=core.page_role_or_redirect
    router=APIRouter()

    @router.get("/api/v38/status")
    def status(req:Request):
        need_login(req)
        return {"version":VERSION,"status":"OK","sources":["MANUAL","WHATSAPP","MAGAZINE","NEWSPAPER"]}

    @router.get("/api/v38/database/{source}")
    def database(source:str,req:Request,q:str="",limit:int=Query(300,ge=1,le=2000)):
        need_login(req);src=source.upper()
        if src=="MANUAL": rows=_manual_rows(engine,limit)
        elif src=="MAGAZINE": rows=_magazine_rows(engine,limit)
        elif src=="NEWSPAPER": rows=_newspaper_rows(engine,limit)
        elif src=="WHATSAPP": rows=_wa_properties(limit)
        else: raise HTTPException(404,"Unknown source")
        props=[_normalize_property(r,src) for r in rows]
        if q.strip():
            qq=_norm(q)
            props=[p for p in props if qq in _norm(" ".join(str(p.get(k) or "") for k in ["property_name","location","contact_name","contact_phone","source_name"]))]
        return {"version":VERSION,"source":src,"count":len(props),"rows":props}

    @router.get("/api/v38/whatsapp-centre")
    def whatsapp_centre(req:Request,limit:int=Query(300,ge=1,le=1000)):
        need_login(req);w=_wa_engine()
        if w is None:return {"status":"OFFLINE","requirements":[],"availabilities":[],"matches":[]}
        with w.connect() as c:
            reqs=_serialize(c.execute(text("""SELECT r.*,s.group_name source_group
              FROM wa_requirements r LEFT JOIN wa_sources s ON s.source_id=r.source_id
              WHERE COALESCE(r.status,'ACTIVE')='ACTIVE' ORDER BY r.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all())
            props=_serialize(c.execute(text("""SELECT p.*,s.group_name source_group
              FROM wa_properties p LEFT JOIN wa_sources s ON s.source_id=p.source_id
              WHERE COALESCE(p.record_status,'ACTIVE')='ACTIVE' ORDER BY p.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all())
            matches=_serialize(c.execute(text("""SELECT m.*,
              r.company_name,r.client_name,r.preferred_locations,r.minimum_area_sqft,r.maximum_area_sqft,
              r.contact_name requirement_contact_name,r.contact_phone requirement_contact_phone,
              p.location property_location,p.area_sqft,p.available_area_sqft,p.rent_inr,p.property_type,
              COALESCE(p.owner_name,p.broker_name,p.sender_name) property_contact_name,
              COALESCE(p.owner_phone,p.broker_phone,p.sender_phone) property_contact_phone,
              s.group_name property_group
              FROM wa_matches m
              JOIN wa_requirements r ON r.wa_requirement_id=m.wa_requirement_id
              JOIN wa_properties p ON p.wa_property_id=m.wa_property_id
              LEFT JOIN wa_sources s ON s.source_id=p.source_id
              WHERE m.score>=70
              ORDER BY m.score DESC,m.created_at DESC LIMIT :lim"""),{"lim":limit}).mappings().all())
        return {"status":"OK","requirements":reqs,"availabilities":props,"matches":matches}

    @router.post("/api/v38/match")
    def match(req:Request,payload:dict=Body(...)):
        need_login(req)
        selected=[str(x).upper() for x in payload.get("sources",[]) if str(x).upper() in SOURCE_COLORS]
        if not selected:selected=["MANUAL","WHATSAPP","MAGAZINE","NEWSPAPER"]
        threshold=float(payload.get("minimum_score") or 70)
        requirement={
            "location":payload.get("location"),
            "area_min":payload.get("area_min"),
            "area_max":payload.get("area_max"),
            "property_type":payload.get("property_type"),
            "transaction":payload.get("transaction") or "LEASE",
            "budget_max":payload.get("budget_max"),
        }
        allp=[]
        if "MANUAL" in selected: allp += [_normalize_property(r,"MANUAL") for r in _manual_rows(engine,5000)]
        if "WHATSAPP" in selected: allp += [_normalize_property(r,"WHATSAPP") for r in _wa_properties(5000)]
        if "MAGAZINE" in selected: allp += [_normalize_property(r,"MAGAZINE") for r in _magazine_rows(engine,5000)]
        if "NEWSPAPER" in selected: allp += [_normalize_property(r,"NEWSPAPER") for r in _newspaper_rows(engine,5000)]
        matches=[]
        for p in allp:
            s,reasons=_score(requirement,p)
            if s>=threshold:
                item=dict(p);item["match_score"]=s;item["match_reasons"]=reasons
                item.pop("raw",None);matches.append(item)
        matches.sort(key=lambda x:x["match_score"],reverse=True)
        counts={s:sum(1 for x in allp if x["source"]==s) for s in selected}
        return {"version":VERSION,"requirement":requirement,"sources":selected,"properties_checked":len(allp),
                "matching_properties":len(matches),"source_counts":counts,"matches":matches[:500]}

    @router.get("/v38/intelligence-workspace",response_class=HTMLResponse)
    def workspace(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE)

    app.include_router(router)
    return router

PAGE=r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance V3.8 Intelligence Workspace</title><style>
:root{--bg:#efe4d2;--card:#fffdf9;--line:#dccdbb;--text:#2c261f;--nav:#5d4937;--blue:#2f6fed;--green:#16845b;--purple:#7c4dff;--orange:#d98200}
*{box-sizing:border-box}body{margin:0;font-family:Arial;background:var(--bg);color:var(--text)}header{background:var(--nav);color:white;padding:16px 20px;position:sticky;top:0;z-index:5}
.wrap{max-width:1600px;margin:auto;padding:18px}.nav{display:flex;gap:8px;flex-wrap:wrap}.btn,button{border:0;border-radius:9px;padding:10px 12px;font-weight:800;cursor:pointer;text-decoration:none;background:#fff;color:#382f26}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.tabs button.active{background:var(--nav);color:white}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.src{padding:10px;border-radius:10px;color:#fff;font-weight:800}.MANUAL{background:var(--blue)}.WHATSAPP{background:var(--green)}.MAGAZINE{background:var(--purple)}.NEWSPAPER{background:var(--orange)}
input,select{padding:10px;border:1px solid var(--line);border-radius:8px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}.tablewrap{overflow:auto;max-height:620px;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:1200px;background:white}th,td{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;font-size:12px;vertical-align:top}th{background:#f8efe3;position:sticky;top:0}
.hidden{display:none}.score{font-size:18px;font-weight:900;color:#146c49}.small{font-size:11px;color:#766957}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style></head><body><header><div class=nav><a class=btn href="/workspace">← Dashboard</a><a class=btn href="/v38/intelligence-workspace">V3.8 Intelligence Workspace</a><a class=btn href="/whatsapp-live">WhatsApp Live</a></div></header>
<div class=wrap><div class=tabs><button class=active onclick="tab('home',this)">Overview</button><button onclick="tab('wa',this)">WhatsApp Requirements & Matches</button><button onclick="tab('matcher',this)">Match Properties</button><button onclick="showDB('MANUAL',this)">Manual Database</button><button onclick="showDB('MAGAZINE',this)">Magazine Database</button><button onclick="showDB('NEWSPAPER',this)">Newspaper Database</button></div>

<section id=home><div class=card><h2>Alliance Source-Aware Intelligence</h2><p>Requirements, availabilities and contacts are visible. The matcher returns matched properties only and shows the source for every result.</p>
<div class=grid><div class="src WHATSAPP">WhatsApp Requirements<br>+ Availabilities + Matches</div><div class="src MANUAL">Manual Property Database</div><div class="src MAGAZINE">Magazine Database</div><div class="src NEWSPAPER">Newspaper Database</div></div></div></section>

<section id=wa class=hidden><div class=card><h2>WhatsApp Requirements ↔ Availabilities ↔ Matches</h2><button onclick=loadWA()>Refresh</button>
<h3>Matches</h3><div class=tablewrap><table><thead><tr><th>Score</th><th>Requirement</th><th>Requirement Contact</th><th>Availability</th><th>Property Contact</th><th>Group / Source</th><th>Reasons</th></tr></thead><tbody id=wamatches></tbody></table></div>
<h3>Requirements</h3><div class=tablewrap><table><thead><tr><th>ID</th><th>Company/Client</th><th>Location</th><th>Area</th><th>Contact</th><th>Phone</th><th>Source Group</th><th>Captured</th></tr></thead><tbody id=wareqs></tbody></table></div>
<h3>Availabilities</h3><div class=tablewrap><table><thead><tr><th>ID</th><th>Location</th><th>Area</th><th>Rent</th><th>Type</th><th>Contact</th><th>Phone</th><th>Source Group</th><th>Captured</th></tr></thead><tbody id=waprops></tbody></table></div></div></section>

<section id=matcher class=hidden><div class=card><h2>AI Multi-Source Property Matcher</h2><p>Only properties at or above the selected match score are shown.</p>
<div class=toolbar><input id=loc placeholder="Required location"><input id=amin placeholder="Min sqft"><input id=amax placeholder="Max sqft"><input id=ptype placeholder="Property type"><select id=txn><option>LEASE</option><option>SALE</option></select><input id=budget placeholder="Max monthly rent"><select id=minscore><option>70</option><option>80</option><option>90</option></select></div>
<div class=toolbar><label><input type=checkbox class=source value=MANUAL checked> Manual</label><label><input type=checkbox class=source value=WHATSAPP checked> WhatsApp</label><label><input type=checkbox class=source value=MAGAZINE checked> Magazine</label><label><input type=checkbox class=source value=NEWSPAPER checked> Newspaper</label><button onclick=runMatch()>FIND MATCHING PROPERTIES</button></div>
<div id=summary class=small></div><div class=tablewrap><table><thead><tr><th>Match</th><th>Source</th><th>Property</th><th>Location</th><th>Area</th><th>Rent</th><th>Type</th><th>Contact</th><th>Phone</th><th>Verification</th><th>Captured</th><th>Reasons</th></tr></thead><tbody id=matchrows></tbody></table></div></div></section>

<section id=db class=hidden><div class=card><h2 id=dbtitle>Database</h2><div class=toolbar><input id=dbq placeholder="Search property/location/contact/phone"><button onclick=loadDB()>Search</button></div><div id=dbcount class=small></div><div class=tablewrap><table><thead><tr><th>Source</th><th>Property</th><th>Location</th><th>Area</th><th>Rent/Price</th><th>Type</th><th>Contact</th><th>Phone</th><th>Verification</th><th>Captured/Updated</th></tr></thead><tbody id=dbrows></tbody></table></div></div></section>
</div><script>
const E=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));let current='MANUAL';
function tab(id,b){document.querySelectorAll('section').forEach(x=>x.classList.add('hidden'));document.getElementById(id).classList.remove('hidden');document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));if(b)b.classList.add('active');if(id==='wa')loadWA()}
function showDB(s,b){current=s;tab('db',b);dbtitle.textContent=s+' DATABASE';loadDB()}
async function J(u,o={}){let r=await fetch(u,{credentials:'include',...o});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={detail:t}};if(!r.ok)throw Error(d.detail||t);return d}
async function loadWA(){let d=await J('/api/v38/whatsapp-centre');wareqs.innerHTML=(d.requirements||[]).map(r=>`<tr><td>${E(r.wa_requirement_id)}</td><td>${E(r.company_name||r.client_name)}</td><td>${E(r.preferred_locations)}</td><td>${E(r.minimum_area_sqft)}-${E(r.maximum_area_sqft)}</td><td>${E(r.contact_name)}</td><td><b>${E(r.contact_phone)}</b></td><td>${E(r.source_group)}</td><td>${E(r.created_at)}</td></tr>`).join('');waprops.innerHTML=(d.availabilities||[]).map(p=>`<tr><td>${E(p.wa_property_id)}</td><td>${E(p.location)}</td><td>${E(p.available_area_sqft||p.area_sqft)}</td><td>${E(p.rent_inr)}</td><td>${E(p.property_type)}</td><td>${E(p.owner_name||p.broker_name||p.sender_name)}</td><td><b>${E(p.owner_phone||p.broker_phone||p.sender_phone)}</b></td><td>${E(p.source_group)}</td><td>${E(p.first_seen||p.created_at)}</td></tr>`).join('');wamatches.innerHTML=(d.matches||[]).map(m=>`<tr><td class=score>${E(m.score)}%</td><td>${E(m.company_name||m.client_name)}<br>${E(m.preferred_locations)}<br>${E(m.minimum_area_sqft)}-${E(m.maximum_area_sqft)}</td><td>${E(m.requirement_contact_name)}<br><b>${E(m.requirement_contact_phone)}</b></td><td>${E(m.property_location)}<br>${E(m.available_area_sqft||m.area_sqft)} sqft<br>₹${E(m.rent_inr)}</td><td>${E(m.property_contact_name)}<br><b>${E(m.property_contact_phone)}</b></td><td><span class="src WHATSAPP">${E(m.property_group||'WhatsApp')}</span></td><td>${E(JSON.stringify(m.reasons||''))}</td></tr>`).join('')}
async function runMatch(){let sources=[...document.querySelectorAll('.source:checked')].map(x=>x.value);let p={location:loc.value,area_min:amin.value,area_max:amax.value,property_type:ptype.value,transaction:txn.value,budget_max:budget.value,minimum_score:minscore.value,sources};let d=await J('/api/v38/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});summary.textContent=`Checked ${d.properties_checked} properties · Matching ${d.matching_properties} · Sources: ${d.sources.join(', ')}`;matchrows.innerHTML=(d.matches||[]).map(x=>`<tr><td class=score>${E(x.match_score)}%</td><td><span class="src ${E(x.source)}">${E(x.source)}</span><br><span class=small>${E(x.source_name)}</span></td><td>${E(x.property_name)}<br><span class=small>${E(x.source_record_id)}</span></td><td>${E(x.location)}</td><td>${E(x.area_min)}-${E(x.area_max)}</td><td>${E(x.rent)}</td><td>${E(x.property_type)}</td><td>${E(x.contact_name)}</td><td><b>${E(x.contact_phone)}</b></td><td>${E(x.verification)}</td><td>${E(x.captured_at)}</td><td>${E((x.match_reasons||[]).join('; '))}</td></tr>`).join('')||'<tr><td colspan=12>No matching properties at this score.</td></tr>'}
async function loadDB(){let d=await J('/api/v38/database/'+current+'?q='+encodeURIComponent(dbq.value||'')+'&limit=500');dbcount.textContent=d.count+' records';dbrows.innerHTML=(d.rows||[]).map(x=>`<tr><td><span class="src ${E(x.source)}">${E(x.source)}</span><br><span class=small>${E(x.source_name)}</span></td><td>${E(x.property_name)}<br><span class=small>${E(x.source_record_id)}</span></td><td>${E(x.location)}</td><td>${E(x.area_min)}-${E(x.area_max)}</td><td>${E(x.rent)}</td><td>${E(x.property_type)}</td><td>${E(x.contact_name)}</td><td><b>${E(x.contact_phone)}</b></td><td>${E(x.verification)}</td><td>${E(x.captured_at)}</td></tr>`).join('')||'<tr><td colspan=10>No records found.</td></tr>'}
</script></body></html>"""
