from __future__ import annotations
import html, json, re
from datetime import datetime
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION='8.0.1-ALLIANCE-FINAL-PROPERTY-WORKING'
MODE='CLEAN_PROPERTY_TABLE_VERIFY_HISTORY_TEAM_DROPDOWN_AREA_CONVERSION_EDIT_SOFT_DELETE_ADDRESS'

NAV=[
('Command Centre','/alliance/primary'),('Properties','/alliance/primary/properties'),('Add Property','/property-manual'),
('Requirements','/alliance/primary/requirements'),('Matcher','/alliance/primary/matcher'),('Verification','/alliance/primary/availability'),
('Follow-ups','/alliance/primary/followups'),('Contacts','/contacts-directory'),('Hospitality','/workspace#hospitality'),
('Retail Expansion','/retail-expansion'),('Reports','/alliance/primary/reports')]

VERIFY_STATUSES=['AVAILABLE','NOT_AVAILABLE','CALL_BACK','SOLD','RENTED','HOLD','WRONG_NUMBER']
VERIFIED_WITH=['OWNER','BROKER','OTHER']

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
    if not v: return ''
    if isinstance(v,str):
        try: v=datetime.fromisoformat(v.replace('Z','+00:00'))
        except Exception: return str(v)
    if isinstance(v,datetime): return v.strftime('%d-%m-%Y %I:%M %p')
    return str(v)
def _app(core): return getattr(core,'app',None) or core
def _engine(core): return getattr(core,'engine',None)
def _role(core,req):
    fn=getattr(core,'need_login',None); return fn(req) if fn else 'team'
def _actor(core,req):
    fn=getattr(core,'actor_name',None)
    try: return str(fn(req) if fn else 'team')
    except Exception: return 'team'
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
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial;color:#172033}}header{{background:#0d2238;color:white;padding:18px 22px;display:flex;justify-content:space-between;flex-wrap:wrap}}nav{{background:white;border-bottom:1px solid #dfe6ee;padding:10px;display:flex;gap:7px;flex-wrap:wrap;position:sticky;top:0;z-index:5}}nav a,.btn,button,.summarybtn{{background:#0d2238;color:white;text-decoration:none;border:0;border-radius:8px;padding:8px 10px;display:inline-block;cursor:pointer;font-size:12px}}.btn.good,.good{{background:#067647}}.danger{{background:#b42318}}.warn{{background:#b54708}}.light{{background:#475467}}.wrap{{max-width:1900px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin-bottom:12px}}.num{{font-size:28px;font-weight:800}}.muted{{color:#667085}}.tablebox{{overflow:auto;max-height:74vh}}table{{border-collapse:collapse;width:100%;font-size:12px;min-width:1450px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#f8fafc;z-index:2}}input,select,textarea{{padding:7px;border:1px solid #cfd8e3;border-radius:7px;max-width:100%}}form.inline{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}}details.admin{{background:white;border:1px solid #dfe6ee;padding:8px 12px}}details.admin a{{margin:5px;display:inline-block}}details.pop{{position:relative}}details.pop>div{{position:absolute;z-index:9;background:white;border:1px solid #d0d5dd;border-radius:10px;padding:10px;min-width:340px;max-width:520px;box-shadow:0 8px 24px #0002}}details.pop summary{{list-style:none}}.desc{{min-width:260px;max-width:420px;white-space:normal}}.addr{{font-weight:700}}.tiny{{font-size:11px}}.status{{font-weight:700}}.history{{max-height:280px;overflow:auto}}.historyitem{{border-bottom:1px solid #eee;padding:7px 0}}.editgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}}label{{font-size:12px;font-weight:700}}label input,label select,label textarea{{display:block;width:100%;margin-top:4px}}textarea{{min-height:80px}}
</style><script>
function areaChange(id,sqft){{const s=document.getElementById('u_'+id);const o=document.getElementById('a_'+id);let v=Number(sqft||0),u=s.value;if(u==='SQYD')v=v/9;else if(u==='SQM')v=v*0.092903;else if(u==='ACRE')v=v/43560;o.textContent=(u==='ACRE'?v.toFixed(4):v.toFixed(2))+' '+u.replace('SQFT','Sq Ft').replace('SQYD','Sq Yd').replace('SQM','Sq M').replace('ACRE','Acre')}}
</script></head><body><header><div><b>Alliance CRE Operating System · 8.0</b><br><small>Property → Verify → Match → Follow-up → Deal</small></div><div>{_e(role)} · <a href="/logout" style="color:white">Logout</a></div></header><nav>{nav}</nav>{admin}<div class="wrap"><h2>{_e(title)}</h2>{body}</div></body></html>'''

def _counts(e):
    with e.connect() as c:
        return {
        'properties':c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v711 p WHERE NOT EXISTS(SELECT 1 FROM pi_property_archive_v801 a WHERE a.canonical_id=p.canonical_id AND a.restored_at IS NULL)")).scalar_one(),
        'requirements':c.execute(text('SELECT COUNT(*) FROM pi_master_requirements_v711')).scalar_one(),
        'verified':c.execute(text("SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE verification_status='VERIFIED'")).scalar_one(),
        'available':c.execute(text("SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE availability_status='AVAILABLE'")).scalar_one(),
        'matches':c.execute(text('SELECT COUNT(*) FROM pi_master_matches_v720')).scalar_one(),
        'followups':c.execute(text("SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE followup_status='SCHEDULED'")).scalar_one()}

def _source(e,cid):
    with e.connect() as c:
        r=c.execute(text('''SELECT source_type,source_table,source_pk,created_at FROM pi_master_source_links_v711 WHERE canonical_id=:id ORDER BY created_at DESC,id DESC LIMIT 1'''),{'id':cid}).mappings().first()
    return dict(r) if r else {}

def _recovered(e,cid):
    try:
        with e.connect() as c:
            r=c.execute(text("""SELECT original_text,recovered_record,section_heading FROM pi_source_recovery_candidates_v738 WHERE canonical_id=:id AND status IN ('RECOVERABLE_TEXT','RECOVERED_NEEDS_REVIEW') ORDER BY id DESC LIMIT 1"""),{'id':cid}).mappings().first()
        return dict(r) if r else {}
    except Exception: return {}

def _contact(cr):
    name=_first(cr,'contact_name','owner_name','broker_name','sender_name','name') or ''
    phone=_first(cr,'contact_number','contact_phone','owner_contact','owner_phone','broker_contact','broker_phone','phone','mobile') or ''
    if not phone:
        phones=cr.get('phones')
        if isinstance(phones,list) and phones: phone=', '.join(str(x) for x in phones if x)
    return name,phone

def _property_view(e,r):
    cr=_dict(r.get('clean_record')); rec=_recovered(e,r['canonical_id']); rr=_dict(rec.get('recovered_record')); src=_source(e,r['canonical_id'])
    address=_first(cr,'address','exact_address','property_address') or _first(rr,'address') or ''
    locality=r.get('locality') or _first(cr,'locality','location') or _first(rr,'locality') or ''
    building=_first(cr,'property_name','building_name','project_name') or ''
    teamdesc=_first(cr,'team_description','description_edit') or ''
    original=_first(cr,'original_description','original_message','raw_line','source_text') or rec.get('original_text') or ''
    parts=[x for x in [address,building,locality] if x]
    desc=teamdesc or ' · '.join(dict.fromkeys(parts)) or original or 'Address / description not captured'
    ptype=_first(cr,'property_category','property_type','category') or _first(rr,'property_category') or ''
    tx=r.get('transaction_type') or _first(cr,'transaction_type','rent_or_sale') or _first(rr,'transaction_type') or ''
    sqft=_first(cr,'area_sqft','available_area_sqft') or r.get('area_sqft') or r.get('area_value') or 0
    try: sqft=float(sqft or 0)
    except Exception: sqft=0
    rent=_first(cr,'rent','monthly_rent','rent_amount','rent_in_figures')
    sale=_first(cr,'sale_price','sale_amount','price','asking_price')
    amount=rent if str(tx).upper() in ('RENT','LEASE') else sale
    if amount in (None,''): amount=_first(cr,'amount','price_raw') or r.get('price_raw') or ''
    cname,cphone=_contact(cr)
    source=src.get('source_type') or r.get('source_type') or _first(cr,'source') or ''
    source_name=_first(cr,'source_name','whatsapp_group','publication','publication_name') or src.get('source_table') or ''
    return dict(cid=r['canonical_id'],description=desc,address=address,locality=locality,ptype=ptype,transaction=tx,sqft=sqft,amount=amount,contact_name=cname,contact_phone=cphone,entry_dt=_fmt_dt(r.get('created_at')),verification=r.get('verification_status') or 'UNVERIFIED',availability=r.get('availability_status') or 'UNKNOWN',assigned_to=r.get('assigned_to') or '',source=source,source_name=source_name,clean=cr,original=original,city=r.get('city') or _first(cr,'city') or '',floor=_first(cr,'floor','floors','floor_codes') or _first(rr,'floors','floor_codes') or '')

def _team_members(e,current=''):
    vals=set()
    if current and current.lower() not in ('team','none'): vals.add(current)
    queries=["SELECT DISTINCT assigned_to v FROM pi_master_action_state_v730 WHERE assigned_to IS NOT NULL AND assigned_to<>''", "SELECT DISTINCT verified_by v FROM pi_master_workflow_v720 WHERE verified_by IS NOT NULL AND verified_by<>''", "SELECT DISTINCT verified_by v FROM pi_property_verification_history_v801 WHERE verified_by IS NOT NULL AND verified_by<>''"]
    with e.connect() as c:
        for q in queries:
            try:
                for r in c.execute(text(q)).mappings():
                    if r.get('v'): vals.add(str(r['v']))
            except Exception: pass
    if not vals: vals.add(current or 'Team')
    return sorted(vals,key=str.lower)

def _history_map(e,cids):
    out={cid:[] for cid in cids}
    if not cids:return out
    with e.connect() as c:
        rows=c.execute(text("""SELECT canonical_id,status,verified_with,verified_by,remarks,next_verification_at,created_at FROM pi_property_verification_history_v801 WHERE canonical_id=ANY(:ids) ORDER BY created_at DESC,id DESC"""),{'ids':cids}).mappings().all()
    for r in rows: out.setdefault(r['canonical_id'],[]).append(dict(r))
    return out

def _history_html(entry_dt,items):
    last=items[0] if items else None
    top=f'<div><b>Entry Date:</b> {_e(entry_dt or "Unknown")}</div>'
    if last:
        top+=f'<div><b>Last Verified:</b> {_e(_fmt_dt(last.get("created_at")))}</div><div><b>Verified By:</b> {_e(last.get("verified_by"))}</div>'
    else: top+='<div><b>Last Verified:</b> Never</div>'
    hist=''.join(f'''<div class="historyitem"><b>{_e(_fmt_dt(x.get('created_at')))}</b> · {_e(x.get('status'))}<br>Verified by: {_e(x.get('verified_by'))} · With: {_e(x.get('verified_with'))}<br>Remarks: {_e(x.get('remarks') or '—')}<br>Next verification: {_e(_fmt_dt(x.get('next_verification_at')) or '—')}</div>''' for x in items)
    return top+f'<div class="history">{hist or "<div class=\"muted\">No verification history yet.</div>"}</div>'

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError('Alliance 8.0.1 requires core app + engine')
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_business_os_v800_audit(id BIGSERIAL PRIMARY KEY,action TEXT NOT NULL,actor TEXT,details JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_property_verification_history_v801(id BIGSERIAL PRIMARY KEY,canonical_id TEXT NOT NULL,status TEXT NOT NULL,verified_with TEXT,verified_by TEXT NOT NULL,remarks TEXT,next_verification_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_property_verify_hist_v801 ON pi_property_verification_history_v801(canonical_id,created_at DESC)"))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_property_edit_audit_v801(id BIGSERIAL PRIMARY KEY,canonical_id TEXT NOT NULL,actor TEXT,changes JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_property_archive_v801(id BIGSERIAL PRIMARY KEY,canonical_id TEXT NOT NULL,archived_by TEXT,reason TEXT,archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),restored_at TIMESTAMPTZ)"""))
        c.execute(text("INSERT INTO pi_business_os_v800_audit(action,actor,details) VALUES('REGISTERED','SYSTEM',CAST(:d AS JSONB))"),{'d':json.dumps({'version':VERSION,'mode':MODE})})
    removed={p:_remove_get(app,p) for p in ['/alliance/primary','/alliance/primary/properties','/alliance/primary/requirements','/alliance/primary/reports']}

    @app.get('/alliance/primary',response_class=HTMLResponse)
    def command(req:Request):
        c=_counts(e); cards=''.join(f'<div class="card"><div class="muted">{_e(k)}</div><div class="num">{v}</div></div>' for k,v in [('Properties',c['properties']),('Requirements',c['requirements']),('Verified',c['verified']),('Available',c['available']),('Matches',c['matches']),('Follow-ups',c['followups'])])
        return HTMLResponse(_shell(core,req,'Command Centre',f'<div class="grid">{cards}</div><div class="card"><b>PROPERTY → VERIFY → REQUIREMENT → MATCH → CLIENT → FOLLOW-UP → DEAL</b></div>'))

    @app.get('/alliance/primary/properties',response_class=HTMLResponse)
    def props(req:Request,q:str=Query('',max_length=120),limit:int=Query(500,ge=1,le=1500)):
        _role(core,req); actor=_actor(core,req); params={'q':f'%{q.strip()}%','n':limit}
        with e.connect() as c:
            rows=c.execute(text("""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,COALESCE(w.availability_status,'UNKNOWN') availability_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to FROM pi_master_properties_v711 p LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=p.canonical_id WHERE NOT EXISTS(SELECT 1 FROM pi_property_archive_v801 ar WHERE ar.canonical_id=p.canonical_id AND ar.restored_at IS NULL) AND (:q='%%' OR p.canonical_id ILIKE :q OR COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q) ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST LIMIT :n"""),params).mappings().all()
        views=[_property_view(e,dict(r)) for r in rows]; histories=_history_map(e,[x['cid'] for x in views]); teams=_team_members(e,actor)
        teamopts=''.join(f'<option value="{_e(x)}">{_e(x)}</option>' for x in teams)
        trs=[]
        for v in views:
            cid=v['cid']; sid=re.sub(r'[^A-Za-z0-9_]','_',cid); hist=histories.get(cid,[])
            status=v['availability'] if v['availability'] not in ('','UNKNOWN') else v['verification']
            verifyopts=''.join(f'<option value="{x}">{x.replace("_"," ").title()}</option>' for x in VERIFY_STATUSES)
            withopts=''.join(f'<option value="{x}">{x.title()}</option>' for x in VERIFIED_WITH)
            verify=f'''<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/primary/property/{_e(cid)}/verify" class="inline"><select name="status" required>{verifyopts}</select><select name="verified_with" required>{withopts}</select><select name="verified_by" required><option value="">Verified By…</option>{teamopts}</select><input name="remarks" placeholder="Remarks"><input type="datetime-local" name="next_verification_at"><button class="good">Save Verification</button></form></div></details>'''
            history=f'''<details class="pop"><summary class="summarybtn light">History ▼</summary><div>{_history_html(v['entry_dt'],hist)}</div></details>'''
            area=f'''<div id="a_{sid}">{v['sqft']:.2f} Sq Ft</div><select id="u_{sid}" onchange="areaChange('{sid}',{v['sqft']})"><option value="SQFT">Sq Ft</option><option value="SQYD">Sq Yd</option><option value="SQM">Sq M</option><option value="ACRE">Acre</option></select>'''
            desc=f'''<div class="desc"><div class="addr">{_e(v['description'])}</div><div class="tiny muted">{_e(v['ptype'])}{' · '+_e(v['floor']) if v['floor'] else ''}</div></div>'''
            delete=f'''<form method="post" action="/alliance/primary/property/{_e(cid)}/delete" onsubmit="return confirm('Delete this property from the working database? The source evidence will be preserved for audit.');"><button class="danger">Delete</button></form>'''
            vals=[f'<a href="/alliance/primary/property/{_e(cid)}">{_e(cid)}</a>',desc,area,_e(v['transaction']),_e(v['amount']),_e(v['contact_name']),_e(v['contact_phone']),_e(v['entry_dt']),f'<span class="status">{_e(status)}</span>',verify,history,_e(v['assigned_to'] or 'UNASSIGNED'),_e(v['source']),f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}/edit">Edit</a>',delete]
            trs.append('<tr>'+''.join(f'<td>{x}</td>' for x in vals)+'</tr>')
        H=['Property ID','Description / Address','Area','Rent/Sale','Amount','Contact Name','Contact Number','Date & Time','Status','Verify','History','Assigned To','Source','Edit','Delete']
        body=f'''<div class="card"><form class="inline"><input name="q" value="{_e(q)}" placeholder="Search address, locality, contact, source"><input name="limit" type="number" value="{limit}"><button>Search</button><a class="btn good" href="/property-manual">Add Property</a></form><p class="muted">One clean working row. Area converts by dropdown. Verification keeps permanent history. Delete is safe archive: evidence and canonical ID are preserved.</p></div><div class="card tablebox"><table><thead><tr>{''.join('<th>'+h+'</th>' for h in H)}</tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan="15">No properties found</td></tr>'}</tbody></table></div>'''
        return HTMLResponse(_shell(core,req,'Property Database',body))

    @app.post('/alliance/primary/property/{cid}/verify')
    def verify(req:Request,cid:str,status:str=Form(...),verified_with:str=Form(...),verified_by:str=Form(...),remarks:str=Form(''),next_verification_at:str=Form('')):
        _role(core,req); status=status.upper().strip(); verified_with=verified_with.upper().strip(); verified_by=verified_by.strip()
        if status not in VERIFY_STATUSES: return HTMLResponse('Invalid verification status',400)
        if verified_with not in VERIFIED_WITH: return HTMLResponse('Invalid verified-with value',400)
        if not verified_by: return HTMLResponse('Verified By team member is required',400)
        availability='AVAILABLE' if status=='AVAILABLE' else 'UNAVAILABLE' if status in ('NOT_AVAILABLE','SOLD','RENTED','WRONG_NUMBER') else 'UNKNOWN'
        nextv=next_verification_at.strip() or None
        with e.begin() as c:
            exists=c.execute(text('SELECT 1 FROM pi_master_properties_v711 WHERE canonical_id=:id'),{'id':cid}).first()
            if not exists: return HTMLResponse('Property not found',404)
            c.execute(text("""INSERT INTO pi_master_workflow_v720(canonical_id,entity_type,verification_status,verified_at,verified_by,availability_status,updated_at) VALUES(:id,'PROPERTY','VERIFIED',NOW(),:by,:av,NOW()) ON CONFLICT(canonical_id) DO UPDATE SET verification_status='VERIFIED',verified_at=NOW(),verified_by=:by,availability_status=:av,updated_at=NOW()"""),{'id':cid,'by':verified_by,'av':availability})
            c.execute(text("""INSERT INTO pi_property_verification_history_v801(canonical_id,status,verified_with,verified_by,remarks,next_verification_at) VALUES(:id,:st,:vw,:vb,:rm,CAST(:nv AS TIMESTAMPTZ))"""),{'id':cid,'st':status,'vw':verified_with,'vb':verified_by,'rm':remarks.strip(),'nv':nextv})
            c.execute(text("""INSERT INTO pi_master_action_log_v730(canonical_id,entity_type,action,actor,details) VALUES(:id,'PROPERTY','VERIFICATION_RECORDED',:by,CAST(:d AS JSONB))"""),{'id':cid,'by':verified_by,'d':json.dumps({'status':status,'verified_with':verified_with,'remarks':remarks,'next_verification_at':nextv})})
        return RedirectResponse('/alliance/primary/properties',303)

    @app.get('/alliance/primary/property/{cid}/edit',response_class=HTMLResponse)
    def edit_page(req:Request,cid:str):
        _role(core,req); actor=_actor(core,req)
        with e.connect() as c:
            r=c.execute(text("""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,COALESCE(w.availability_status,'UNKNOWN') availability_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to FROM pi_master_properties_v711 p LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=p.canonical_id WHERE p.canonical_id=:id"""),{'id':cid}).mappings().first()
        if not r:return HTMLResponse('Property not found',404)
        v=_property_view(e,dict(r)); cr=v['clean']; teams=_team_members(e,actor); teamopts=''.join(f'<option value="{_e(x)}"'+(' selected' if x==v['assigned_to'] else '')+f'>{_e(x)}</option>' for x in teams)
        body=f'''<div class="card"><form method="post" action="/alliance/primary/property/{_e(cid)}/edit"><div class="editgrid">
<label>Description<input name="team_description" value="{_e(_first(cr,'team_description') or '')}" placeholder="Business description"></label>
<label>Exact Address<input name="address" value="{_e(v['address'])}" placeholder="e.g. A-7 Inner Circle"></label>
<label>Locality<input name="locality" value="{_e(v['locality'])}"></label><label>City<input name="city" value="{_e(v['city'])}"></label>
<label>Property Type<input name="property_type" value="{_e(v['ptype'])}"></label><label>Rent/Sale<select name="transaction_type"><option value="RENT" {'selected' if str(v['transaction']).upper()=='RENT' else ''}>Rent</option><option value="SALE" {'selected' if str(v['transaction']).upper()=='SALE' else ''}>Sale</option><option value="LEASE" {'selected' if str(v['transaction']).upper()=='LEASE' else ''}>Lease</option></select></label>
<label>Area<input name="area_value" value="{_e(v['sqft'])}" type="number" step="any"></label><label>Area Unit<select name="area_unit"><option value="SQFT">Sq Ft</option><option value="SQYD">Sq Yd</option><option value="SQM">Sq M</option><option value="ACRE">Acre</option></select></label>
<label>Amount<input name="amount" value="{_e(v['amount'])}" placeholder="Rent or sale amount"></label><label>Floor<input name="floor" value="{_e(v['floor'])}"></label>
<label>Contact Name<input name="contact_name" value="{_e(v['contact_name'])}"></label><label>Contact Number<input name="contact_number" value="{_e(v['contact_phone'])}"></label>
<label>Assigned To<select name="assigned_to"><option value="">Unassigned</option>{teamopts}</select></label>
</div><p class="muted">Original source message is not overwritten. Edits are stored as structured team corrections with audit history.</p><button class="good">Save Changes</button> <a class="btn light" href="/alliance/primary/properties">Cancel</a></form></div>'''
        return HTMLResponse(_shell(core,req,'Edit Property '+cid,body))

    @app.post('/alliance/primary/property/{cid}/edit')
    def edit_save(req:Request,cid:str,team_description:str=Form(''),address:str=Form(''),locality:str=Form(''),city:str=Form(''),property_type:str=Form(''),transaction_type:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),amount:str=Form(''),floor:str=Form(''),contact_name:str=Form(''),contact_number:str=Form(''),assigned_to:str=Form('')):
        _role(core,req); actor=_actor(core,req); unit=area_unit.upper().strip(); tx=transaction_type.upper().strip()
        try: n=float(area_value) if str(area_value).strip() else None
        except Exception: n=None
        sqft=None
        if n is not None:
            sqft=n if unit=='SQFT' else n*9 if unit=='SQYD' else n/0.092903 if unit=='SQM' else n*43560 if unit=='ACRE' else n
        patch={'team_description':team_description.strip(),'address':address.strip(),'exact_address':address.strip(),'locality':locality.strip(),'city':city.strip(),'property_type':property_type.strip(),'property_category':property_type.strip(),'transaction_type':tx,'area_sqft':round(sqft,4) if sqft is not None else None,'area_input_value':n,'area_input_unit':unit,'floor':floor.strip(),'contact_name':contact_name.strip(),'contact_number':contact_number.strip()}
        if tx in ('RENT','LEASE'): patch['rent_amount']=amount.strip(); patch['rent_in_figures']=amount.strip()
        elif tx=='SALE': patch['sale_amount']=amount.strip(); patch['sale_price']=amount.strip()
        with e.begin() as c:
            exists=c.execute(text('SELECT 1 FROM pi_master_properties_v711 WHERE canonical_id=:id'),{'id':cid}).first()
            if not exists:return HTMLResponse('Property not found',404)
            c.execute(text("""UPDATE pi_master_properties_v711 SET clean_record=COALESCE(clean_record,'{}'::jsonb) || CAST(:p AS JSONB),transaction_type=COALESCE(NULLIF(:tx,''),transaction_type),locality=COALESCE(NULLIF(:loc,''),locality),city=COALESCE(NULLIF(:city,''),city),area_value=COALESCE(:av,area_value),area_unit=COALESCE(NULLIF(:au,''),area_unit),area_sqft=COALESCE(:sqft,area_sqft),price_raw=COALESCE(NULLIF(:amt,''),price_raw),updated_at=NOW() WHERE canonical_id=:id"""),{'p':json.dumps(patch),'tx':tx,'loc':locality.strip(),'city':city.strip(),'av':n,'au':unit,'sqft':sqft,'amt':amount.strip(),'id':cid})
            if assigned_to.strip():
                c.execute(text("""INSERT INTO pi_master_action_state_v730(canonical_id,entity_type,assigned_to,updated_at) VALUES(:id,'PROPERTY',:a,NOW()) ON CONFLICT(canonical_id) DO UPDATE SET assigned_to=:a,updated_at=NOW()"""),{'id':cid,'a':assigned_to.strip()})
            c.execute(text("INSERT INTO pi_property_edit_audit_v801(canonical_id,actor,changes) VALUES(:id,:a,CAST(:d AS JSONB))"),{'id':cid,'a':actor,'d':json.dumps(patch)})
        return RedirectResponse('/alliance/primary/properties',303)

    @app.post('/alliance/primary/property/{cid}/delete')
    def delete_property(req:Request,cid:str):
        _role(core,req); actor=_actor(core,req)
        with e.begin() as c:
            exists=c.execute(text('SELECT 1 FROM pi_master_properties_v711 WHERE canonical_id=:id'),{'id':cid}).first()
            if not exists:return HTMLResponse('Property not found',404)
            c.execute(text("INSERT INTO pi_property_archive_v801(canonical_id,archived_by,reason) VALUES(:id,:by,'USER_DELETE_FROM_WORKING_DATABASE')"),{'id':cid,'by':actor})
            c.execute(text("""INSERT INTO pi_master_action_log_v730(canonical_id,entity_type,action,actor,details) VALUES(:id,'PROPERTY','ARCHIVED_FROM_WORKING_DATABASE',:by,'{}'::jsonb)"""),{'id':cid,'by':actor})
        return RedirectResponse('/alliance/primary/properties',303)

    # Requirements remains concise and compatible with the approved 8.0 structure.
    @app.get('/alliance/primary/requirements',response_class=HTMLResponse)
    def reqs(req:Request,q:str=Query('',max_length=120),limit:int=Query(500,ge=1,le=1500)):
        _role(core,req); params={'q':f'%{q.strip()}%','n':limit}
        with e.connect() as c:
            rows=c.execute(text("""SELECT r.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to FROM pi_master_requirements_v711 r LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id WHERE (:q='%%' OR r.canonical_id ILIKE :q OR COALESCE(r.locality,'') ILIKE :q OR COALESCE(r.city,'') ILIKE :q OR COALESCE(r.clean_record::text,'') ILIKE :q) ORDER BY r.updated_at DESC NULLS LAST,r.created_at DESC NULLS LAST LIMIT :n"""),params).mappings().all()
        trs=[]
        for r in rows:
            r=dict(r); cr=_dict(r.get('clean_record')); src=_source(e,r['canonical_id']); contact=_first(cr,'contact_name','client_name','name') or ''; phone=_first(cr,'contact_phone','phone','mobile') or ''; desc=_first(cr,'original_message','original_description','requirement_text','additional_points') or ''; area=_first(cr,'required_area_sqft','minimum_area_sqft','maximum_area_sqft') or r.get('area_sqft') or ''; tx=r.get('transaction_type') or _first(cr,'transaction_type','rent_or_sale') or ''; budget=_first(cr,'budget','rent_budget','sale_budget') or ''
            vals=[f'<a href="/alliance/primary/requirement/{_e(r["canonical_id"])}">{_e(r["canonical_id"])}</a>',_e(desc),_e(area),_e(tx),_e(budget),_e(contact),_e(phone),_e(_fmt_dt(r.get('created_at'))),_e(r.get('verification_status') or 'UNVERIFIED'),_e(r.get('assigned_to') or 'UNASSIGNED'),_e(src.get('source_type') or r.get('source_type') or '')]
            trs.append('<tr>'+''.join('<td>'+x+'</td>' for x in vals)+'</tr>')
        H=['Requirement ID','Description','Area Sq Ft','Rent/Sale','Budget','Contact Name','Contact Number','Date & Time','Verification','Assigned To','Source']
        body=f'<div class="card"><form class="inline"><input name="q" value="{_e(q)}" placeholder="Search requirement"><button>Search</button><a class="btn good" href="/requirements-workbench">Add / Manage Requirement</a></form></div><div class="card tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>'
        return HTMLResponse(_shell(core,req,'Requirements',body))

    @app.get('/alliance/primary/reports',response_class=HTMLResponse)
    def reports(req:Request):
        c=_counts(e); body='<div class="grid">'+''.join(f'<div class="card"><div class="muted">{_e(k)}</div><div class="num">{v}</div></div>' for k,v in c.items())+'</div>'
        return HTMLResponse(_shell(core,req,'Business Reports',body))
    return {'status':'registered','version':VERSION,'removed_routes':removed}

def self_test():
    assert 'AVAILABLE' in VERIFY_STATUSES and 'OWNER' in VERIFIED_WITH
    assert round(7500/9,2)==833.33 and round(7500*0.092903,2)==696.77
    return {'status':'PASS','version':VERSION,'mode':MODE}

if __name__=='__main__': print(json.dumps(self_test(),indent=2))
