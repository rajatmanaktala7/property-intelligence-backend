from __future__ import annotations
import html, json, uuid, datetime
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
import alliance_full_property_database_v1170 as base

VERSION='11.7.1-MANUAL-ENTRY-FULL-COUNTS-NAV'
SOURCES=base.SOURCES
UNITS=base.UNITS

def e(v): return html.escape('' if v is None else str(v))

def shell(body,k=''):
    tabs=''.join(f'<a class="{"on" if k==x else ""}" href="/alliance/source/{x}">{cfg[1]}</a>' for x,cfg in SOURCES.items())
    css='''*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#172033;font:12px Arial}header{background:#102a43;color:#fff;padding:14px 18px}.top{background:#fff3cd;border-bottom:1px solid #d6b656;padding:8px 12px}nav,.tabs{background:#fff;border-bottom:1px solid #98a2b3;padding:7px;white-space:nowrap;overflow:auto}a{text-decoration:none}nav a,.tabs a,.btn,button,.back{display:inline-block;background:#102a43;color:#fff;padding:7px 9px;margin:2px;border:0}.back{background:#1f6f43;font-weight:bold}.tabs a.on{background:#486581}.danger{background:#9b1c1c}.wrap{padding:10px}.kpis{display:flex;gap:6px;flex-wrap:wrap}.kpi{display:inline-flex;flex-direction:column;background:#fff;border:1px solid #667085;padding:10px 16px}.kpi b{font-size:24px}.search{display:flex;gap:5px;margin:8px 0}.search input{min-width:320px}.pager{margin:8px 0;background:#fff;border:1px solid #98a2b3;padding:7px}.tablebox{overflow:auto;max-height:70vh;border:1px solid #667085}table{border-collapse:collapse;width:max-content;min-width:100%;background:#fff}th,td{border:1px solid #98a2b3;padding:5px 6px;vertical-align:top;overflow-wrap:anywhere}th{background:#e9eef5;position:sticky;top:0;z-index:2}td.desc{min-width:360px;max-width:520px;white-space:pre-wrap}input,select,textarea{border:1px solid #98a2b3;padding:7px;width:100%}textarea{min-height:110px}.grid{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:8px;background:#fff;border:1px solid #98a2b3;padding:12px}.wide{grid-column:1/-1}.thumb{max-width:240px;max-height:180px;margin:6px;border:1px solid #98a2b3}.hint{color:#667085;font-size:11px}@media(max-width:800px){.grid{grid-template-columns:1fr}.search{display:block}.search input{min-width:0}}'''
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>{css}</style></head><body><header><b>Alliance CRE Intelligence OS 11.7.1</b><br>Full Property Database</header><div class="top"><a class="back" href="/alliance/primary">← Back to Dashboard</a></div><nav><a href="/alliance/property-add/manual">+ Add Manual Property</a><a href="/alliance/source/manual">Property Databases</a><a href="/commercial-intelligence">Commercial Intelligence</a></nav><div class="tabs">{tabs}</div><div class="wrap">{body}</div></body></html>'''

def source_data(engine,k,q,page,per_page):
    t,_,pk=SOURCES[k]; off=(page-1)*per_page
    with engine.connect() as c:
        total=int(c.execute(text(f'''SELECT COUNT(*) FROM "{t}" x WHERE NOT EXISTS(SELECT 1 FROM ai_source_record_archives a WHERE a.source_type=:s AND a.source_record_id=CAST(x."{pk}" AS text))'''),{'s':k}).scalar() or 0)
        filtered=total
        if q:
            filtered=int(c.execute(text(f'''SELECT COUNT(*) FROM "{t}" x WHERE NOT EXISTS(SELECT 1 FROM ai_source_record_archives a WHERE a.source_type=:s AND a.source_record_id=CAST(x."{pk}" AS text)) AND to_jsonb(x)::text ILIKE :pat'''),{'s':k,'pat':'%'+q+'%'}).scalar() or 0)
        rs=c.execute(text(f'''SELECT to_jsonb(x) FROM "{t}" x WHERE NOT EXISTS(SELECT 1 FROM ai_source_record_archives a WHERE a.source_type=:s AND a.source_record_id=CAST(x."{pk}" AS text)) AND (:q='' OR to_jsonb(x)::text ILIKE :pat) ORDER BY x."{pk}" DESC NULLS LAST LIMIT :lim OFFSET :off'''),{'s':k,'q':q,'pat':'%'+q+'%','lim':per_page,'off':off}).scalars().all()
    return total,filtered,[r if isinstance(r,dict) else json.loads(r) for r in rs]

def listing(engine,k,q,page,per_page):
    total,filtered,rs=source_data(engine,k,q,page,per_page)
    pages=max(1,(filtered+per_page-1)//per_page); page=min(max(1,page),pages)
    heads=['Property ID','Location','Description','Category','Type','Area','Unit','Floor','Rent/Sale Amount','Owner/Broker','Contact No.','Entry Date','Verification','Posted By','Assigned To','Source','Photos','Videos','Edit','Archive']
    trs=[]
    for d in rs:
        n=base.norm(k,d); rid=str(d.get(SOURCES[k][2],'')); ph,vi=base.media_count(engine,k,d); cells=[]
        for i,x in enumerate(n): cells.append(f'<td class="{"desc" if i==2 else ""}">{e(x)}</td>')
        u=f'/alliance/property-media/{k}/{quote(rid,safe="")}'
        cells += [f'<td><a class="btn" href="{u}">Photos ({ph})</a></td>',f'<td><a class="btn" href="{u}">Videos ({vi})</a></td>',f'<td><a class="btn" href="/alliance/property-edit/{k}/{quote(rid,safe="")}">Edit</a></td>',f'<td><form method="post" action="/alliance/property-archive/{k}/{quote(rid,safe="")}" onsubmit="return confirm(\'Archive record?\')"><button class="danger">Archive</button></form></td>']
        trs.append('<tr>'+''.join(cells)+'</tr>')
    lo=0 if filtered==0 else (page-1)*per_page+1; hi=min(page*per_page,filtered); b=f'/alliance/source/{k}?q={quote(q)}&per_page={per_page}'
    prev=f'<a class="btn" href="{b}&page={page-1}">← Previous</a>' if page>1 else ''
    nxt=f'<a class="btn" href="{b}&page={page+1}">Next →</a>' if page<pages else ''
    opts=''.join(f'<option value="{x}" {"selected" if x==per_page else ""}>{x}</option>' for x in (50,100,200,500))
    body=f'''<div class="kpis"><div class="kpi"><b>{total:,}</b><span>FULL DATABASE TOTAL</span></div><div class="kpi"><b>{filtered:,}</b><span>{'Search results' if q else 'Active records'}</span></div><div class="kpi"><b>{lo:,}–{hi:,}</b><span>Showing now</span></div></div><form class="search"><input name="q" value="{e(q)}" placeholder="Search complete {SOURCES[k][1]} database"><select name="per_page" style="width:110px">{opts}</select><input type="hidden" name="page" value="1"><button>Search</button></form><div class="pager">Page <b>{page:,}</b> of <b>{pages:,}</b> · Showing {lo:,}–{hi:,} of {filtered:,} {prev}{nxt}</div><div class="tablebox"><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan="20">No records</td></tr>'}</tbody></table></div><div class="pager">{prev} Page {page:,} of {pages:,} {nxt}</div>'''
    return shell(body,k)

def add_form():
    units=''.join(f'<option>{u}</option>' for u in UNITS)
    return shell(f'''<h2>Add Manual Property</h2><p class="hint">One property = one operational record. Photos/videos stay linked to the same property code.</p><form class="grid" method="post" enctype="multipart/form-data"><label>Location *<input name="location" required></label><label>City<input name="city" value="Delhi NCR"></label><label>Category *<select name="category"><option>Commercial Rent</option><option>Commercial Sale</option><option>Residential Rent</option><option>Residential Sale</option><option>Industrial Rent</option><option>Industrial Sale</option><option>Farmhouse Rent</option><option>Farmhouse Sale</option></select></label><label>Property Type<input name="ptype"></label><label>Area Value<input name="area" inputmode="decimal"></label><label>Area Unit<select name="unit">{units}</select></label><label>Floor<input name="floor"></label><label>Rent Amount<input name="rent"></label><label>Sale Amount<input name="sale"></label><label>Owner / Broker<input name="contact"></label><label>Contact No.<input name="phone"></label><label>Verification<select name="verification"><option>UNVERIFIED</option><option>VERIFIED</option><option>AVAILABLE</option><option>NOT AVAILABLE</option></select></label><label>Posted By *<input name="posted" required></label><label>Assigned To<input name="assigned"></label><label class="wide">Description<textarea name="description"></textarea></label><label class="wide">Photos / Videos<input type="file" name="media" multiple accept="image/*,video/*"><span class="hint">Select or drag multiple files.</span></label><div class="wide"><button>Save Property</button> <a class="btn" href="/alliance/primary">Cancel</a></div></form>''','manual')

def number(v):
    try:return float(str(v).replace(',','').strip()) if str(v).strip() else None
    except:return None

def to_sqft(v,u): return None if v is None else v*{'Sq Ft':1,'Sq Yd':9,'Sq Mtr':10.7639104167,'Acre':43560}.get(u,1)

async def save_manual(engine,req):
    f=await req.form(); now=datetime.datetime.now(datetime.timezone.utc); code='MAN-'+now.strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6].upper(); area=number(f.get('area','')); unit=str(f.get('unit','Sq Ft')); rent=number(f.get('rent','')); sale=number(f.get('sale','')); ptype=str(f.get('ptype','')).strip()
    p={'code':code,'name':str(f.get('description',''))[:180],'pt':json.dumps([ptype] if ptype else []),'city':str(f.get('city','')),'loc':str(f.get('location','')),'sqft':to_sqft(area,unit),'rent':rent,'cat':str(f.get('category','Commercial Rent')),'floor':str(f.get('floor','')),'contact':str(f.get('contact','')),'phone':str(f.get('phone','')),'ver':str(f.get('verification','UNVERIFIED')),'desc':str(f.get('description','')),'posted':str(f.get('posted','')),'area_input':str(f.get('area','')),'unit':unit,'area_text':(str(f.get('area',''))+' '+unit).strip(),'rent_text':str(f.get('rent','')),'area':area,'sale':sale,'sale_text':str(f.get('sale',''))}
    with engine.begin() as c:
        rid=c.execute(text('''INSERT INTO pi_operational_properties(property_code,division,property_name,property_types,city,location,area_sqft,rent_amount,transaction_type,floor,owner_broker_name,contact_number,verification_status,remarks,created_by,created_at,updated_at,entry_source,entered_by,entry_date,area_input,area_unit,area_text,rent_text,area_value,sale_amount,rent_input_text,sale_input_text) VALUES(:code,'DELHI_NCR',:name,CAST(:pt AS jsonb),:city,:loc,:sqft,:rent,:cat,:floor,:contact,:phone,:ver,:desc,:posted,now(),now(),'MANUAL',:posted,now(),:area_input,:unit,:area_text,:rent_text,:area,:sale,:rent_text,:sale_text) RETURNING id'''),p).scalar()
        for key,val in f.multi_items():
            if key!='media' or not getattr(val,'filename',None): continue
            content=await val.read()
            if not content: continue
            mime=getattr(val,'content_type',None) or 'application/octet-stream'; mt='video' if mime.startswith('video/') else 'image'
            c.execute(text('''INSERT INTO pi_operational_property_media(property_code,media_type,filename,mime_type,file_size,content,created_at) VALUES(:code,:mt,:fn,:mime,:sz,:content,now())'''),{'code':code,'mt':mt,'fn':val.filename,'mime':mime,'sz':len(content),'content':content})
    return rid

def register(wrapped):
    app=wrapped.app; core=wrapped.core; engine=core.engine
    base.shell=shell
    for k in SOURCES:
        path=f'/alliance/source/{k}'; base.rm(app,path,{'GET'})
        def page(req:Request,q:str='',page:int=1,per_page:int=100,_k=k):
            core.need_login(req); return HTMLResponse(listing(engine,_k,q.strip(),max(1,page),max(1,min(per_page,500))),headers={'Cache-Control':'no-store','X-Alliance-CRE-Version':VERSION})
        app.add_api_route(path,page,methods=['GET'],include_in_schema=False)
    base.rm(app,'/alliance/final/database/manual',{'GET'})
    def legacy(req:Request,q:str='',page:int=1,per_page:int=100):
        core.need_login(req); return HTMLResponse(listing(engine,'manual',q.strip(),max(1,page),max(1,min(per_page,500))))
    app.add_api_route('/alliance/final/database/manual',legacy,methods=['GET'],include_in_schema=False)
    for path in ('/property-manual','/manual-property-v18','/manual-property','/alliance/property-add/manual'):
        base.rm(app,path,{'GET','POST'})
        def get_add(req:Request): core.need_login(req); return HTMLResponse(add_form(),headers={'Cache-Control':'no-store'})
        async def post_add(req:Request): core.need_login(req); rid=await save_manual(engine,req); return RedirectResponse(f'/alliance/property-edit/manual/{rid}',303)
        app.add_api_route(path,get_add,methods=['GET'],include_in_schema=False); app.add_api_route(path,post_add,methods=['POST'],include_in_schema=False)
    preferred={f'/alliance/source/{k}' for k in SOURCES}|{'/alliance/final/database/manual','/property-manual','/manual-property-v18','/manual-property','/alliance/property-add/manual'}
    chosen=[r for r in list(app.router.routes) if getattr(r,'path',None) in preferred]
    for r in chosen:
        try:app.router.routes.remove(r)
        except ValueError:pass
    for r in reversed(chosen):app.router.routes.insert(0,r)
    return {'status':'REGISTERED','version':VERSION}
