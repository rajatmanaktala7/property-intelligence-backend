import os, json, uuid, html
from typing import Optional
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, text

router = APIRouter(prefix='/whatsapp-capture', tags=['WhatsApp Capture V8'])
WA_DATABASE_URL = os.getenv('WHATSAPP_DATABASE_URL','').strip()

def _db_url(url):
    if url.startswith('postgres://'):
        return url.replace('postgres://','postgresql+psycopg://',1)
    if url.startswith('postgresql://'):
        return url.replace('postgresql://','postgresql+psycopg://',1)
    return url

engine = create_engine(_db_url(WA_DATABASE_URL), pool_pre_ping=True, pool_recycle=300) if WA_DATABASE_URL else None

V8_SCHEMA = '''
CREATE TABLE IF NOT EXISTS v8_manual_requirements(
 id BIGSERIAL PRIMARY KEY,
 requirement_id TEXT UNIQUE NOT NULL,
 client_name TEXT, company_name TEXT, contact_phone TEXT,
 transaction_type TEXT, property_type TEXT, city TEXT, preferred_location TEXT,
 minimum_area_sqft NUMERIC(14,2), maximum_area_sqft NUMERIC(14,2),
 budget_min_inr NUMERIC(16,2), budget_max_inr NUMERIC(16,2),
 floor_preference TEXT, frontage_requirement TEXT, parking_requirement TEXT,
 nearby_brands TEXT, possession TEXT, suitable_category TEXT,
 source TEXT DEFAULT 'MANUAL', team_member TEXT, remarks TEXT,
 status TEXT DEFAULT 'ACTIVE', created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS v8_manual_matches(
 id BIGSERIAL PRIMARY KEY,
 requirement_id TEXT NOT NULL, wa_property_id TEXT NOT NULL,
 score NUMERIC(5,2), grade TEXT, reasons JSONB DEFAULT '[]'::jsonb,
 created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(requirement_id,wa_property_id)
);
CREATE TABLE IF NOT EXISTS v8_bridge_commands(
 id BIGSERIAL PRIMARY KEY,
 command_id TEXT UNIQUE NOT NULL, account_phone TEXT, command_type TEXT NOT NULL,
 status TEXT DEFAULT 'PENDING', requested_by TEXT, requested_at TIMESTAMPTZ DEFAULT NOW(),
 acknowledged_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, result JSONB DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS v8_audit_log(
 id BIGSERIAL PRIMARY KEY, action TEXT NOT NULL, entity_type TEXT, entity_id TEXT,
 details JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_v8_req_status ON v8_manual_requirements(status);
CREATE INDEX IF NOT EXISTS idx_v8_match_req ON v8_manual_matches(requirement_id);
CREATE INDEX IF NOT EXISTS idx_v8_command_status ON v8_bridge_commands(status,account_phone);
'''

def require_db():
    if engine is None:
        raise HTTPException(503,'WHATSAPP_DATABASE_URL is not configured.')

def init_v8():
    require_db()
    with engine.begin() as c:
        for stmt in [x.strip() for x in V8_SCHEMA.split(';') if x.strip()]:
            c.execute(text(stmt))

@router.on_event('startup')
def startup():
    if engine is not None:
        try: init_v8()
        except Exception as e: print('WhatsApp Capture V8 init warning:',e)

def esc(v):
    return html.escape('' if v is None else str(v))

def money(v):
    if v in (None,'','UNKNOWN'): return '—'
    try:
        n=float(v)
        if n>=10_000_000:return f'₹{n/10_000_000:.2f} Cr'
        if n>=100_000:return f'₹{n/100_000:.2f} L'
        return f'₹{n:,.0f}'
    except:return esc(v)

def sim(a,b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None,str(a or '').lower().strip(),str(b or '').lower().strip()).ratio()

def num_sim(a,b):
    try:
        a=float(a); b=float(b)
        if not a or not b:return 0
        return max(0,1-abs(a-b)/max(abs(a),abs(b)))
    except:return 0

def score_requirement(req,prop):
    score=0; reasons=[]
    rl=str(req.get('preferred_location') or '').lower(); pl=str(prop.get('location') or '').lower()
    if rl and pl and (rl in pl or pl in rl):score+=30;reasons.append('Strong location match')
    elif rl and pl and sim(rl,pl)>=.70:score+=22;reasons.append('Similar location')
    a=prop.get('area_sqft'); mn=req.get('minimum_area_sqft'); mx=req.get('maximum_area_sqft')
    if a and mn and mx and float(mn)<=float(a)<=float(mx):score+=20;reasons.append('Area fits')
    elif a and (mn or mx):
        if num_sim(a,float(mn or mx))>=.80:score+=12;reasons.append('Area near requirement')
    rt=str(req.get('property_type') or ''); pt=str(prop.get('property_type') or '')
    if rt and rt!='UNKNOWN' and rt==pt:score+=10;reasons.append('Property type')
    rtx=str(req.get('transaction_type') or ''); ptx=str(prop.get('transaction_type') or '')
    if rtx and (rtx==ptx or ptx=='SALE_RENT'):score+=10;reasons.append('Transaction')
    budget=req.get('budget_max_inr'); price=prop.get('rent_inr') if rtx=='RENT' else prop.get('sale_price_inr')
    if budget and price and float(price)<=float(budget):score+=15;reasons.append('Within budget')
    if prop.get('verification_status')=='VERIFIED_AVAILABLE':score+=10;reasons.append('Verified available')
    elif prop.get('availability')=='AVAILABLE':score+=5;reasons.append('Availability signal')
    if prop.get('sender_phone') or prop.get('broker_phone') or prop.get('owner_phone'):score+=5;reasons.append('Contact available')
    grade='EXCELLENT' if score>=90 else 'STRONG' if score>=80 else 'POSSIBLE' if score>=70 else 'WEAK'
    return min(score,100),grade,reasons

def shell(title,body,active='Dashboard'):
    nav=[('Dashboard','/whatsapp-capture'),('WhatsApp Sources','/whatsapp-capture/sources'),('Property Database','/whatsapp-capture/properties'),('Requirements','/whatsapp-capture/requirements'),('AI Matches','/whatsapp-capture/matches'),('Verification','/whatsapp-capture/verification'),('System Health','/whatsapp-capture/system-health')]
    links=''.join(f'<a class="{"active" if n==active else ""}" href="{u}">{esc(n)}</a>' for n,u in nav)
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,Arial,sans-serif;background:#f5f7fa;color:#101828}}header{{background:#101828;color:white;padding:18px 24px}}header h1{{margin:0;font-size:23px}}header small{{color:#98a2b3}}nav{{display:flex;gap:6px;flex-wrap:wrap;background:white;padding:10px 18px;border-bottom:1px solid #e4e7ec;position:sticky;top:0;z-index:10}}nav a{{text-decoration:none;color:#344054;padding:9px 12px;border-radius:8px}}nav a.active,nav a:hover{{background:#101828;color:white}}main{{max-width:1550px;margin:22px auto;padding:0 18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}}.card{{background:white;border:1px solid #e4e7ec;border-radius:12px;padding:17px}}.num{{font-size:30px;font-weight:750}}.muted{{color:#667085}}.btn{{display:inline-block;border:0;background:#101828;color:white;padding:10px 14px;border-radius:8px;text-decoration:none;cursor:pointer}}.btn2{{background:#175cd3}}.btn3{{background:#039855}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}}th{{background:#f9fafb;position:sticky;top:58px}}.scroll{{overflow:auto;max-height:72vh;border:1px solid #e4e7ec;border-radius:12px}}input,select,textarea{{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:8px;background:white}}form.gridform{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}.full{{grid-column:1/-1}}.pill{{padding:4px 8px;border-radius:999px;background:#eef4ff;color:#3538cd;display:inline-block}}</style></head><body><header><h1>Alliance WhatsApp Property Capture V8</h1><small>Additive team workspace · existing property and WhatsApp fields remain untouched</small></header><nav>{links}<a href="/workspace">← Main Workspace</a></nav><main>{body}</main></body></html>'''

@router.get('',response_class=HTMLResponse)
def dashboard():
    require_db(); init_v8()
    with engine.begin() as c:
        qs={'WhatsApp Messages':'SELECT COUNT(*) FROM wa_messages','Clean Properties':"SELECT COUNT(*) FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE' AND duplicate_status<>'DUPLICATE'",'WhatsApp Requirements':'SELECT COUNT(*) FROM wa_requirements','Manual Requirements':"SELECT COUNT(*) FROM v8_manual_requirements WHERE status='ACTIVE'",'Verified Properties':"SELECT COUNT(*) FROM wa_properties WHERE verification_status='VERIFIED_AVAILABLE' AND COALESCE(record_status,'ACTIVE')='ACTIVE'",'Pending Bridge Commands':"SELECT COUNT(*) FROM v8_bridge_commands WHERE status='PENDING'"}
        stats={}
        for k,q in qs.items():
            try:stats[k]=c.execute(text(q)).scalar() or 0
            except:stats[k]=0
        recent=c.execute(text('SELECT requirement_id,client_name,preferred_location,property_type,minimum_area_sqft,maximum_area_sqft FROM v8_manual_requirements ORDER BY id DESC LIMIT 8')).mappings().all()
    cards=''.join(f'<div class=card><div class=muted>{esc(k)}</div><div class=num>{v}</div></div>' for k,v in stats.items())
    rows=''.join(f"<tr><td>{esc(r['requirement_id'])}</td><td>{esc(r['client_name'])}</td><td>{esc(r['preferred_location'])}</td><td>{esc(r['property_type'])}</td><td>{esc(r['minimum_area_sqft'])}–{esc(r['maximum_area_sqft'])}</td><td><a class=btn href='/whatsapp-capture/requirements/{esc(r['requirement_id'])}/matches'>Find Matches</a></td></tr>" for r in recent)
    body=f'''<div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><div><h2>Team Command Centre</h2><p class=muted>V8 reads existing WhatsApp/property tables and writes only to new v8_* tables.</p></div><a class="btn btn3" href="/whatsapp-capture/requirements/new">+ Add Offline Requirement</a></div><div class=grid>{cards}</div><h3>Recent Manual Requirements</h3><div class=scroll><table><tr><th>ID</th><th>Client</th><th>Location</th><th>Type</th><th>Area</th><th></th></tr>{rows}</table></div>'''
    return HTMLResponse(shell('WhatsApp Capture V8',body,'Dashboard'))

@router.get('/sources',response_class=HTMLResponse)
def sources():
    require_db()
    with engine.begin() as c:
        rows=c.execute(text('SELECT s.group_name,COUNT(*) AS messages,MAX(m.created_at) AS last_seen FROM wa_messages m LEFT JOIN wa_sources s ON s.source_id=m.source_id GROUP BY s.group_name ORDER BY messages DESC')).mappings().all()
    trs=''.join(f"<tr><td>{esc(r['group_name'])}</td><td>{r['messages']}</td><td>{esc(r['last_seen'])}</td></tr>" for r in rows)
    return HTMLResponse(shell('WhatsApp Sources',f'<h2>WhatsApp Sources</h2><p class=muted>Read-only view of existing WhatsApp data.</p><div class=scroll><table><tr><th>Group</th><th>Messages</th><th>Last Seen</th></tr>{trs}</table></div>','WhatsApp Sources'))

@router.get('/properties',response_class=HTMLResponse)
def properties():
    require_db()
    with engine.begin() as c:
        rows=c.execute(text("""SELECT wa_property_id,raw_text,COALESCE(broker_phone,owner_phone,sender_phone) AS contact,CASE WHEN transaction_type='RENT' THEN rent_inr ELSE sale_price_inr END AS price,area_sqft,location,property_type,verification_status FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE' AND duplicate_status<>'DUPLICATE' ORDER BY id DESC LIMIT 1000""")).mappings().all()
    trs=''.join(f"<tr><td style='min-width:360px'>{esc(r['raw_text'])}</td><td>{esc(r['contact'])}</td><td>{money(r['price'])}</td><td>{esc(r['area_sqft'])}</td><td>{esc(r['location'])}</td><td>{esc(r['property_type'])}</td><td>{esc(r['verification_status'])}</td><td><a class=btn href='/whatsapp-intelligence/property/{esc(r['wa_property_id'])}'>Open</a></td></tr>" for r in rows)
    return HTMLResponse(shell('Property Database',f'<h2>Property Database</h2><p class=muted>Existing WhatsApp property records. No field/schema changes.</p><div class=scroll><table><tr><th>Raw Message</th><th>Contact</th><th>Price/Rent</th><th>Area</th><th>Location</th><th>Type</th><th>Verification</th><th></th></tr>{trs}</table></div>','Property Database'))

@router.get('/requirements',response_class=HTMLResponse)
def requirements():
    require_db()
    with engine.begin() as c:
        manual=c.execute(text('SELECT * FROM v8_manual_requirements ORDER BY id DESC LIMIT 1000')).mappings().all()
        wa=c.execute(text("SELECT wa_requirement_id AS requirement_id,client_name,contact_phone,preferred_locations AS preferred_location,property_type,minimum_area_sqft,maximum_area_sqft,budget_max_inr FROM wa_requirements ORDER BY id DESC LIMIT 500")).mappings().all()
    mtrs=''.join(f"<tr><td>{esc(r['requirement_id'])}</td><td>{esc(r['client_name'])}</td><td>{esc(r['contact_phone'])}</td><td>{esc(r['preferred_location'])}</td><td>{esc(r['property_type'])}</td><td>{esc(r['minimum_area_sqft'])}–{esc(r['maximum_area_sqft'])}</td><td>{money(r['budget_max_inr'])}</td><td><a class=btn href='/whatsapp-capture/requirements/{esc(r['requirement_id'])}/matches'>Match</a></td></tr>" for r in manual)
    wtrs=''.join(f"<tr><td>{esc(r['requirement_id'])}</td><td>{esc(r['client_name'])}</td><td>{esc(r['contact_phone'])}</td><td>{esc(r['preferred_location'])}</td><td>{esc(r['property_type'])}</td><td>{esc(r['minimum_area_sqft'])}–{esc(r['maximum_area_sqft'])}</td><td>{money(r['budget_max_inr'])}</td><td><a class=btn href='/whatsapp-intelligence/requirement/{esc(r['requirement_id'])}/matches'>Existing Matcher</a></td></tr>" for r in wa)
    body=f"<div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap'><h2>Requirements</h2><a class='btn btn3' href='/whatsapp-capture/requirements/new'>+ Add Offline Requirement</a></div><h3>Manual / Offline</h3><div class=scroll><table><tr><th>ID</th><th>Client</th><th>Phone</th><th>Location</th><th>Type</th><th>Area</th><th>Budget</th><th></th></tr>{mtrs}</table></div><h3>WhatsApp Extracted</h3><div class=scroll><table><tr><th>ID</th><th>Client</th><th>Phone</th><th>Location</th><th>Type</th><th>Area</th><th>Budget</th><th></th></tr>{wtrs}</table></div>"
    return HTMLResponse(shell('Requirements',body,'Requirements'))

@router.get('/requirements/new',response_class=HTMLResponse)
def new_requirement():
    body='''<h2>Add Offline / Manual Requirement</h2><div class=card><form class=gridform method=post><div><label>Client / Retailer Name</label><input name=client_name required></div><div><label>Company / Brand</label><input name=company_name></div><div><label>Contact Number</label><input name=contact_phone></div><div><label>Transaction</label><select name=transaction_type><option>RENT</option><option>SALE</option></select></div><div><label>Property Type</label><input name=property_type></div><div><label>City</label><input name=city value="Delhi NCR"></div><div><label>Preferred Location / Micro-market</label><input name=preferred_location required></div><div><label>Minimum Area Sq Ft</label><input type=number step=0.01 name=minimum_area_sqft></div><div><label>Maximum Area Sq Ft</label><input type=number step=0.01 name=maximum_area_sqft></div><div><label>Budget Min INR</label><input type=number step=0.01 name=budget_min_inr></div><div><label>Budget / Rent Max INR</label><input type=number step=0.01 name=budget_max_inr></div><div><label>Floor Preference</label><input name=floor_preference></div><div><label>Frontage Requirement</label><input name=frontage_requirement></div><div><label>Parking</label><input name=parking_requirement></div><div><label>Nearby Brands</label><input name=nearby_brands></div><div><label>Possession</label><input name=possession></div><div><label>Suitable Category</label><input name=suitable_category></div><div><label>Source</label><input name=source value="OFFLINE / CALL"></div><div><label>Team Member</label><input name=team_member></div><div class=full><label>Remarks / Additional Points</label><textarea rows=4 name=remarks></textarea></div><div class=full><button class="btn btn3" type=submit>SAVE & FIND MATCHES</button></div></form></div>'''
    return HTMLResponse(shell('Add Requirement',body,'Requirements'))

@router.post('/requirements/new')
def create_requirement(client_name:str=Form(...),company_name:str=Form(''),contact_phone:str=Form(''),transaction_type:str=Form('RENT'),property_type:str=Form(''),city:str=Form(''),preferred_location:str=Form(...),minimum_area_sqft:Optional[float]=Form(None),maximum_area_sqft:Optional[float]=Form(None),budget_min_inr:Optional[float]=Form(None),budget_max_inr:Optional[float]=Form(None),floor_preference:str=Form(''),frontage_requirement:str=Form(''),parking_requirement:str=Form(''),nearby_brands:str=Form(''),possession:str=Form(''),suitable_category:str=Form(''),source:str=Form('OFFLINE / CALL'),team_member:str=Form(''),remarks:str=Form('')):
    require_db();init_v8();rid='V8R-'+uuid.uuid4().hex[:10].upper()
    with engine.begin() as c:
        c.execute(text('''INSERT INTO v8_manual_requirements(requirement_id,client_name,company_name,contact_phone,transaction_type,property_type,city,preferred_location,minimum_area_sqft,maximum_area_sqft,budget_min_inr,budget_max_inr,floor_preference,frontage_requirement,parking_requirement,nearby_brands,possession,suitable_category,source,team_member,remarks) VALUES(:rid,:client,:company,:phone,:tx,:ptype,:city,:loc,:mn,:mx,:bmin,:bmax,:floor,:frontage,:parking,:brands,:possession,:category,:source,:team,:remarks)'''),{'rid':rid,'client':client_name,'company':company_name,'phone':contact_phone,'tx':transaction_type,'ptype':property_type or 'UNKNOWN','city':city or 'UNKNOWN','loc':preferred_location,'mn':minimum_area_sqft,'mx':maximum_area_sqft,'bmin':budget_min_inr,'bmax':budget_max_inr,'floor':floor_preference,'frontage':frontage_requirement,'parking':parking_requirement,'brands':nearby_brands,'possession':possession,'category':suitable_category,'source':source,'team':team_member,'remarks':remarks})
        c.execute(text("INSERT INTO v8_audit_log(action,entity_type,entity_id,details) VALUES('MANUAL_REQUIREMENT_CREATED','REQUIREMENT',:rid,CAST(:details AS JSONB))"),{'rid':rid,'details':json.dumps({'client_name':client_name,'source':source,'team_member':team_member})})
    return RedirectResponse(f'/whatsapp-capture/requirements/{rid}/matches',303)

@router.get('/requirements/{rid}/matches',response_class=HTMLResponse)
def requirement_matches(rid:str):
    require_db()
    with engine.begin() as c:
        req=c.execute(text('SELECT * FROM v8_manual_requirements WHERE requirement_id=:r'),{'r':rid}).mappings().first()
        if not req:raise HTTPException(404,'Requirement not found')
        props=c.execute(text("""SELECT wa_property_id,raw_text,location,property_type,transaction_type,area_sqft,rent_inr,sale_price_inr,availability,verification_status,broker_name,broker_phone,owner_name,owner_phone,sender_name,sender_phone FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE' AND duplicate_status<>'DUPLICATE' ORDER BY id DESC LIMIT 2500""")).mappings().all()
        scored=[]
        for p in props:
            s,g,reasons=score_requirement(req,p)
            if s>=40:scored.append((s,g,reasons,p))
        scored.sort(key=lambda x:x[0],reverse=True);scored=scored[:100]
        c.execute(text('DELETE FROM v8_manual_matches WHERE requirement_id=:r'),{'r':rid})
        for s,g,reasons,p in scored:
            c.execute(text("""INSERT INTO v8_manual_matches(requirement_id,wa_property_id,score,grade,reasons) VALUES(:r,:p,:s,:g,CAST(:reasons AS JSONB)) ON CONFLICT(requirement_id,wa_property_id) DO UPDATE SET score=EXCLUDED.score,grade=EXCLUDED.grade,reasons=EXCLUDED.reasons,created_at=NOW()"""),{'r':rid,'p':p['wa_property_id'],'s':s,'g':g,'reasons':json.dumps(reasons)})
    trs=''
    for s,g,reasons,p in scored:
        contact=p['broker_phone'] or p['owner_phone'] or p['sender_phone'] or ''
        price=p['rent_inr'] if req['transaction_type']=='RENT' else p['sale_price_inr']
        trs+=f"<tr><td><span class=pill><b>{s:.0f}%</b> {esc(g)}</span></td><td style='min-width:330px'>{esc(p['raw_text'])}</td><td>{esc(contact)}</td><td>{money(price)}</td><td>{esc(p['area_sqft'])}</td><td>{esc(p['location'])}</td><td>{esc(p['verification_status'])}</td><td>{esc(', '.join(reasons))}</td><td><a class=btn href='/whatsapp-intelligence/property/{esc(p['wa_property_id'])}'>Verify</a></td></tr>"
    body=f"<div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap'><div><h2>Matches for {esc(rid)}</h2><p class=muted>{esc(req['client_name'])} · {esc(req['preferred_location'])} · {esc(req['property_type'])}</p></div><a class='btn btn2' href='/whatsapp-capture/requirements/new'>+ New Requirement</a></div><div class=scroll><table><tr><th>Match %</th><th>Raw Property Message</th><th>Contact</th><th>Price/Rent</th><th>Area</th><th>Location</th><th>Verification</th><th>Why Matched</th><th></th></tr>{trs}</table></div>"
    return HTMLResponse(shell('AI Matches',body,'AI Matches'))

@router.get('/matches',response_class=HTMLResponse)
def matches_index():
    require_db()
    with engine.begin() as c:
        rows=c.execute(text('''SELECT m.requirement_id,r.client_name,r.preferred_location,COUNT(*) AS matches,MAX(m.score) AS best_score FROM v8_manual_matches m JOIN v8_manual_requirements r ON r.requirement_id=m.requirement_id GROUP BY m.requirement_id,r.client_name,r.preferred_location ORDER BY MAX(m.created_at) DESC LIMIT 500''')).mappings().all()
    trs=''.join(f"<tr><td>{esc(r['requirement_id'])}</td><td>{esc(r['client_name'])}</td><td>{esc(r['preferred_location'])}</td><td>{r['matches']}</td><td><b>{float(r['best_score'] or 0):.0f}%</b></td><td><a class=btn href='/whatsapp-capture/requirements/{esc(r['requirement_id'])}/matches'>Open</a></td></tr>" for r in rows)
    return HTMLResponse(shell('AI Matches',f'<h2>AI Match Runs</h2><div class=scroll><table><tr><th>Requirement</th><th>Client</th><th>Location</th><th>Matches</th><th>Best Match</th><th></th></tr>{trs}</table></div>','AI Matches'))

@router.get('/verification',response_class=HTMLResponse)
def verification():
    require_db()
    with engine.begin() as c:
        rows=c.execute(text("""SELECT wa_property_id,raw_text,COALESCE(broker_phone,owner_phone,sender_phone) AS contact,location,area_sqft,verification_status FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE' AND duplicate_status<>'DUPLICATE' AND verification_status<>'VERIFIED_AVAILABLE' ORDER BY id DESC LIMIT 1000""")).mappings().all()
    trs=''.join(f"<tr><td style='min-width:360px'>{esc(r['raw_text'])}</td><td>{esc(r['contact'])}</td><td>{esc(r['location'])}</td><td>{esc(r['area_sqft'])}</td><td>{esc(r['verification_status'])}</td><td><a class=btn href='/whatsapp-intelligence/property/{esc(r['wa_property_id'])}'>Verify in Existing Module</a></td></tr>" for r in rows)
    return HTMLResponse(shell('Verification',f'<h2>Verification Queue</h2><p class=muted>V8 does not change your existing verification fields.</p><div class=scroll><table><tr><th>Raw Message</th><th>Contact</th><th>Location</th><th>Area</th><th>Status</th><th></th></tr>{trs}</table></div>','Verification'))

@router.get('/system-health',response_class=HTMLResponse)
def health():
    require_db();checks=[]
    with engine.begin() as c:
        for n,q in [('wa_messages','SELECT COUNT(*) FROM wa_messages'),('wa_properties','SELECT COUNT(*) FROM wa_properties'),('wa_requirements','SELECT COUNT(*) FROM wa_requirements'),('v8_manual_requirements','SELECT COUNT(*) FROM v8_manual_requirements'),('v8_manual_matches','SELECT COUNT(*) FROM v8_manual_matches')]:
            try:v=c.execute(text(q)).scalar() or 0;checks.append((n,'OK',v))
            except Exception as e:checks.append((n,'ERROR',str(e)[:120]))
    trs=''.join(f'<tr><td>{esc(n)}</td><td>{esc(s)}</td><td>{esc(v)}</td></tr>' for n,s,v in checks)
    return HTMLResponse(shell('System Health',f'<h2>System Health</h2><div class=card><b>Safety mode:</b> V8 additive only.</div><div class=scroll><table><tr><th>Component</th><th>Status</th><th>Records / Error</th></tr>{trs}</table></div>','System Health'))
