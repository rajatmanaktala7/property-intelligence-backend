from __future__ import annotations
import html, json
from datetime import datetime
from decimal import Decimal
from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION='8.0.0-ALLIANCE-BUSINESS-PRODUCTION-CONSOLIDATION'
MODE='SAME_DASHBOARD_PRESERVE_APPROVED_FIELDS_UNIVERSAL_DATE_TIME_BUSINESS_FIRST'

NAV=[
('Command Centre','/alliance/primary'),('Properties','/alliance/primary/properties'),('Add Property','/property-manual'),
('Requirements','/alliance/primary/requirements'),('Matcher','/alliance/primary/matcher'),('Verification','/alliance/primary/availability'),
('Follow-ups','/alliance/primary/followups'),('Contacts','/contacts-directory'),('Hospitality','/workspace#hospitality'),
('Retail Expansion','/retail-expansion'),('Reports','/alliance/primary/reports')]

def _e(v): return html.escape('' if v is None else str(v))
def _dict(v):
    if isinstance(v,dict): return v
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception: return {}
    return {}
def _first(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,'',[],{}): return v
    return None
def _fmt_dt(v):
    if not v: return ('','')
    if isinstance(v,str):
        try: v=datetime.fromisoformat(v.replace('Z','+00:00'))
        except Exception: return (str(v),'')
    if isinstance(v,datetime): return (v.strftime('%d-%m-%Y'),v.strftime('%I:%M %p'))
    return (str(v),'')
def _app(core): return getattr(core,'app',None) or core
def _engine(core): return getattr(core,'engine',None)
def _role(core,req):
    fn=getattr(core,'need_login',None); return fn(req) if fn else 'team'
def _remove_get(app,path):
    keep=[]; removed=0
    for r in list(getattr(app,'routes',[])):
        if getattr(r,'path',None)==path and 'GET' in set(getattr(r,'methods',set()) or set()): removed+=1
        else: keep.append(r)
    app.router.routes[:]=keep
    return removed

def _shell(core,req,title,body):
    role=_role(core,req)
    nav=''.join(f'<a href="{_e(p)}">{_e(l)}</a>' for l,p in NAV)
    admin=''
    if role=='admin':
        admin='''<details class="admin"><summary>Admin / Data & System</summary><a href="/alliance/primary/data-repair">Historical Data Repair</a><a href="/alliance/primary/source-recovery">Source Recovery</a><a href="/status-page">System Status</a><a href="/workspace">Legacy Workspace</a></details>'''
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial;color:#172033}}header{{background:#0d2238;color:white;padding:18px 22px;display:flex;justify-content:space-between;flex-wrap:wrap}}nav{{background:white;border-bottom:1px solid #dfe6ee;padding:10px;display:flex;gap:7px;flex-wrap:wrap;position:sticky;top:0;z-index:5}}nav a,.btn{{background:#0d2238;color:white;text-decoration:none;border:0;border-radius:8px;padding:9px 11px;display:inline-block}}.btn.good{{background:#067647}}.wrap{{max-width:1900px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin-bottom:12px}}.num{{font-size:28px;font-weight:800}}.muted{{color:#667085}}.tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:100%;font-size:12px;min-width:1500px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#f8fafc}}input{{padding:8px;border:1px solid #cfd8e3;border-radius:7px}}form{{display:flex;gap:7px;flex-wrap:wrap}}details.admin{{background:white;border:1px solid #dfe6ee;padding:8px 12px}}details.admin a{{margin:5px;display:inline-block}}</style></head><body><header><div><b>Alliance CRE Operating System · 8.0</b><br><small>Verified Inventory → Intelligent Match → Follow-up → Deal</small></div><div>{_e(role)} · <a href="/logout" style="color:white">Logout</a></div></header><nav>{nav}</nav>{admin}<div class="wrap"><h2>{_e(title)}</h2>{body}</div></body></html>'''

def _counts(e):
    with e.connect() as c:
        return {
        'properties':c.execute(text('SELECT COUNT(*) FROM pi_master_properties_v711')).scalar_one(),
        'requirements':c.execute(text('SELECT COUNT(*) FROM pi_master_requirements_v711')).scalar_one(),
        'verified':c.execute(text("SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE verification_status='VERIFIED'")).scalar_one(),
        'available':c.execute(text("SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE availability_status='AVAILABLE'")).scalar_one(),
        'matches':c.execute(text('SELECT COUNT(*) FROM pi_master_matches_v720')).scalar_one(),
        'followups':c.execute(text("SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE followup_status='SCHEDULED'")).scalar_one()}

def _source(e,cid):
    with e.connect() as c:
        r=c.execute(text('''SELECT source_type,source_table,source_pk,created_at FROM pi_master_source_links_v711 WHERE canonical_id=:id ORDER BY created_at DESC,id DESC LIMIT 1'''),{'id':cid}).mappings().first()
    return dict(r) if r else {}

def _recovered_text(e,cid):
    try:
        with e.connect() as c:
            r=c.execute(text("""SELECT original_text,recovered_record,section_heading FROM pi_source_recovery_candidates_v738 WHERE canonical_id=:id AND status IN ('RECOVERABLE_TEXT','RECOVERED_NEEDS_REVIEW') ORDER BY id DESC LIMIT 1"""),{'id':cid}).mappings().first()
        return dict(r) if r else {}
    except Exception: return {}

def _pv(e,r):
    cr=_dict(r.get('clean_record')); src=_source(e,r['canonical_id']); rec=_recovered_text(e,r['canonical_id']); rr=_dict(rec.get('recovered_record'))
    cd,ct=_fmt_dt(r.get('created_at')); ud,ut=_fmt_dt(r.get('updated_at')); sd,st=_fmt_dt(_first(cr,'source_datetime','source_timestamp','message_timestamp') or src.get('created_at'))
    sqft=_first(cr,'area_sqft','available_area_sqft') or r.get('area_sqft') or r.get('area_value'); sqyd=_first(cr,'area_sqyd','area_sq_yard'); sqm=_first(cr,'area_sqm','area_sq_m'); acre=_first(cr,'area_acre','acre')
    if sqft:
        try:
            f=float(sqft); sqyd=sqyd or round(f/9,2); sqm=sqm or round(f*0.092903,2); acre=acre or round(f/43560,4)
        except Exception: pass
    return [r['canonical_id'],cd,ct,sd,st,_first(cr,'property_category','property_type','category') or _first(rr,'property_category') or '',r.get('transaction_type') or _first(cr,'transaction_type','rent_or_sale') or _first(rr,'transaction_type') or '',r.get('city') or _first(cr,'city') or '',r.get('locality') or _first(cr,'locality','location') or _first(rr,'locality') or '',_first(cr,'address','exact_address','property_address') or _first(rr,'address') or '',_first(cr,'property_name','building_name','project_name') or '',sqft or '',sqyd or '',sqm or '',acre or '',_first(cr,'floor','floors','floor_codes') or _first(rr,'floors','floor_codes') or '',_first(cr,'rent','monthly_rent','rent_amount','rent_in_figures') or '',_first(cr,'sale_price','sale_amount','price','asking_price') or '',_first(cr,'cam','cam_per_sqft') or '',_first(cr,'possession') or '',_first(cr,'parking') or '',_first(cr,'owner_name') or '',_first(cr,'owner_contact','owner_phone') or '',_first(cr,'broker_name') or '',_first(cr,'broker_contact','broker_phone') or '',r.get('verification_status') or 'UNVERIFIED',r.get('availability_status') or 'UNKNOWN',r.get('assigned_to') or '',src.get('source_type') or r.get('source_type') or _first(cr,'source') or '',_first(cr,'source_name','whatsapp_group','publication','publication_name') or src.get('source_table') or '',_first(cr,'original_description','original_message','raw_line','source_text') or rec.get('original_text') or '',ud,ut]

def _rv(e,r):
    cr=_dict(r.get('clean_record')); src=_source(e,r['canonical_id']); cd,ct=_fmt_dt(r.get('created_at')); ud,ut=_fmt_dt(r.get('updated_at')); sd,st=_fmt_dt(_first(cr,'source_datetime','source_timestamp','message_timestamp') or src.get('created_at'))
    return [r['canonical_id'],cd,ct,sd,st,_first(cr,'company_name','brand_name','brand') or '',_first(cr,'client_name','contact_name','name') or '',_first(cr,'contact_phone','phone','mobile') or '',_first(cr,'contact_email','email') or '',_first(cr,'requirement_type','property_type','category') or '',r.get('transaction_type') or _first(cr,'transaction_type','rent_or_sale') or '',r.get('city') or _first(cr,'city') or '',r.get('locality') or _first(cr,'preferred_locations','location','locality') or '',_first(cr,'minimum_area_sqft','min_area_sqft','required_area_sqft') or '',_first(cr,'maximum_area_sqft','max_area_sqft','required_area_sqft') or '',_first(cr,'budget','rent_budget','budget_rent','sale_budget') or '',_first(cr,'floor_preference','floor') or '',_first(cr,'frontage') or '',_first(cr,'parking') or '',_first(cr,'additional_points','requirement_text','description') or '',src.get('source_type') or r.get('source_type') or _first(cr,'source') or '',_first(cr,'source_name','whatsapp_group','publication') or src.get('source_table') or '',_first(cr,'original_message','original_description','raw_text','requirement_text') or '',r.get('assigned_to') or '',r.get('verification_status') or 'UNVERIFIED',ud,ut]

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError('Alliance 8.0 requires core app + engine')
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_business_os_v800_audit(id BIGSERIAL PRIMARY KEY,action TEXT NOT NULL,actor TEXT,details JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        c.execute(text("INSERT INTO pi_business_os_v800_audit(action,actor,details) VALUES('REGISTERED','SYSTEM',CAST(:d AS JSONB))"),{'d':json.dumps({'version':VERSION,'mode':MODE})})
    removed={p:_remove_get(app,p) for p in ['/alliance/primary','/alliance/primary/properties','/alliance/primary/requirements','/alliance/primary/reports']}

    @app.get('/alliance/primary',response_class=HTMLResponse)
    def command(req:Request):
        c=_counts(e); cards=''.join(f'<div class="card"><div class="muted">{_e(k)}</div><div class="num">{v}</div></div>' for k,v in [('Properties',c['properties']),('Requirements',c['requirements']),('Verified',c['verified']),('Available',c['available']),('Matches',c['matches']),('Follow-ups',c['followups'])])
        body=f'<div class="grid">{cards}</div><div class="card"><h3>Daily Business Flow</h3><p><b>PROPERTY → VERIFY → REQUIREMENT → MATCH → CLIENT → FOLLOW-UP → DEAL</b></p><p class="muted">Yesterday\'s approved fields remain. Date + Time is universal. Dummy technical fields are hidden, not deleted. Missing historical facts are never invented.</p></div>'
        return HTMLResponse(_shell(core,req,'Command Centre',body))

    @app.get('/alliance/primary/properties',response_class=HTMLResponse)
    def props(req:Request,q:str=Query('',max_length=120),limit:int=Query(500,ge=1,le=2000)):
        _role(core,req); params={'q':f'%{q.strip()}%','n':limit}
        with e.connect() as c:
            rows=c.execute(text("""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,COALESCE(w.availability_status,'UNKNOWN') availability_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to FROM pi_master_properties_v711 p LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=p.canonical_id WHERE (:q='%%' OR p.canonical_id ILIKE :q OR COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q) ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST LIMIT :n"""),params).mappings().all()
        H=['Property ID','Date','Time','Source Date','Source Time','Property Type','Rent/Sale','City','Locality','Exact Address','Property / Building','Sq Ft','Sq Yd','Sq M','Acre','Floor','Rent','Sale Amount','CAM','Possession','Parking','Owner Name','Owner Contact','Broker Name','Broker Contact','Verification','Availability','Assigned To','Source','Source Name','Original Description / Message','Updated Date','Updated Time']
        trs=[]
        for r in rows:
            vals=_pv(e,dict(r)); vals[0]=f'<a href="/alliance/primary/property/{_e(vals[0])}">{_e(vals[0])}</a>'; trs.append('<tr>'+''.join(f'<td>{v if i==0 else _e(v)}</td>' for i,v in enumerate(vals))+'</tr>')
        body=f'<div class="card"><form><input name="q" value="{_e(q)}" placeholder="Search property, locality, address, contact, source"><input name="limit" type="number" value="{limit}"><button class="btn">Search</button><a class="btn good" href="/property-manual">Add Property</a></form><p class="muted">Approved business fields preserved. Date and Time visible on every record.</p></div><div class="card tablebox"><table><thead><tr>{"".join("<th>"+_e(h)+"</th>" for h in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td>No records</td></tr>"}</tbody></table></div>'
        return HTMLResponse(_shell(core,req,'Property Database',body))

    @app.get('/alliance/primary/requirements',response_class=HTMLResponse)
    def reqs(req:Request,q:str=Query('',max_length=120),limit:int=Query(500,ge=1,le=2000)):
        _role(core,req); params={'q':f'%{q.strip()}%','n':limit}
        with e.connect() as c:
            rows=c.execute(text("""SELECT r.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to FROM pi_master_requirements_v711 r LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id WHERE (:q='%%' OR r.canonical_id ILIKE :q OR COALESCE(r.locality,'') ILIKE :q OR COALESCE(r.city,'') ILIKE :q OR COALESCE(r.clean_record::text,'') ILIKE :q) ORDER BY r.updated_at DESC NULLS LAST,r.created_at DESC NULLS LAST LIMIT :n"""),params).mappings().all()
        H=['Requirement ID','Date','Time','Source Date','Source Time','Company / Brand','Contact Person','Contact No.','Email','Requirement Type','Rent/Sale','City','Preferred Location','Min Area Sq Ft','Max Area Sq Ft','Budget / Rent','Floor Preference','Frontage','Parking','Requirement Details','Source','Source Name','Original Message','Assigned To','Verification','Updated Date','Updated Time']
        trs=[]
        for r in rows:
            vals=_rv(e,dict(r)); vals[0]=f'<a href="/alliance/primary/requirement/{_e(vals[0])}">{_e(vals[0])}</a>'; trs.append('<tr>'+''.join(f'<td>{v if i==0 else _e(v)}</td>' for i,v in enumerate(vals))+'</tr>')
        body=f'<div class="card"><form><input name="q" value="{_e(q)}" placeholder="Search brand, client, location, phone, source"><input name="limit" type="number" value="{limit}"><button class="btn">Search</button><a class="btn good" href="/requirements-workbench">Add / Manage Requirement</a></form><p class="muted">Date + Time, contacts, source and original requirement message remain visible.</p></div><div class="card tablebox"><table><thead><tr>{"".join("<th>"+_e(h)+"</th>" for h in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td>No records</td></tr>"}</tbody></table></div>'
        return HTMLResponse(_shell(core,req,'Requirements',body))

    @app.get('/alliance/primary/reports',response_class=HTMLResponse)
    def reports(req:Request):
        c=_counts(e); body='<div class="grid">'+''.join(f'<div class="card"><div class="muted">{_e(k)}</div><div class="num">{v}</div></div>' for k,v in [('Properties',c['properties']),('Requirements',c['requirements']),('Verified',c['verified']),('Available',c['available']),('Matches',c['matches']),('Follow-ups',c['followups'])])+'</div><div class="card"><b>Priority:</b> Verify live inventory → Match active demand → Send client-safe options → Follow up until closure.</div>'
        return HTMLResponse(_shell(core,req,'Business Reports',body))
    return {'status':'registered','version':VERSION,'removed_routes':removed}

def self_test():
    d,t=_fmt_dt('2026-09-04T15:46:00+05:30'); assert d=='04-09-2026' and '03:46' in t; return {'status':'PASS','version':VERSION,'date':d,'time':t}

if __name__=='__main__': print(json.dumps(self_test(),indent=2))
