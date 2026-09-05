from __future__ import annotations
import html,json
from urllib.parse import quote
from fastapi import Request,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse,Response
from sqlalchemy import text
VERSION='11.7.0-FULL-PROPERTY-DATABASE'
SOURCES={'manual':('pi_operational_properties','Manual','id'),'newspaper':('pi_newspaper_properties','Newspaper','id'),'magazine':('pi_magazine_master','Magazine','source_id'),'whatsapp':('pi_whatsapp_property_master','WhatsApp','id')}
UNITS=('Sq Ft','Sq Yd','Sq Mtr','Acre')
def e(v): return html.escape('' if v is None else str(v))
def p(d,*ks):
 for k in ks:
  v=d.get(k)
  if v not in (None,'',[],{}): return v
 return ''
def jc(v):
 if not v:return ''
 if isinstance(v,list):return ', '.join(map(str,v))
 if isinstance(v,dict):return ', '.join(map(str,v.values()))
 try:return jc(json.loads(v))
 except:return str(v)
def rm(app,path,methods): app.router.routes[:]=[r for r in app.router.routes if not(getattr(r,'path',None)==path and set(getattr(r,'methods',set()) or set())&set(methods))]
def ensure(engine):
 with engine.begin() as c:c.execute(text('''CREATE TABLE IF NOT EXISTS ai_source_record_archives(id bigserial PRIMARY KEY,source_type text NOT NULL,source_record_id text NOT NULL,archived_by text,archived_at timestamptz NOT NULL DEFAULT now(),reason text,UNIQUE(source_type,source_record_id))'''))
def norm(k,d):
 if k=='manual':
  pt=d.get('property_types',''); pt=json.dumps(pt,ensure_ascii=False) if isinstance(pt,(dict,list)) else pt; sale=str(d.get('transaction_type','')).lower().startswith('sale')
  return [d.get('id'),p(d,'location','city'),p(d,'remarks','property_name'),d.get('transaction_type'),pt,p(d,'area_value','area_input','area_sqft'),p(d,'area_unit') or 'Sq Ft',d.get('floor'),d.get('sale_amount') if sale else d.get('rent_amount'),d.get('owner_broker_name'),d.get('contact_number'),p(d,'entry_date','created_at'),d.get('verification_status'),p(d,'entered_by','created_by'),'',p(d,'entry_source') or 'MANUAL']
 if k=='newspaper': return [d.get('id'),d.get('locality'),' | '.join(str(x) for x in (d.get('configuration_details'),d.get('notes')) if x),d.get('lead_type'),d.get('configuration_details'),d.get('area'),'','',d.get('price'),p(d,'contact_person','agency_brand'),d.get('phone_numbers'),p(d,'date_captured','created_at'),d.get('verification'),d.get('team_member'),d.get('team_member'),d.get('source') or 'NEWSPAPER']
 if k=='magazine': return [d.get('source_id'),p(d,'locality','locality_source'),d.get('original_raw_text'),d.get('category'),p(d,'listing_type','configuration'),d.get('area'),d.get('area_unit'),d.get('floor'),d.get('price'),d.get('contact_name_company'),jc(d.get('valid_mobiles')) or jc(d.get('valid_landlines')) or jc(d.get('partial_contacts')),d.get('imported_at'),d.get('record_status'),d.get('import_batch'),'','MAGAZINE']
 return [d.get('id'),'',p(d,'description','raw_message'),d.get('lead_type'),d.get('configuration_details'),d.get('area'),'',d.get('floor'),d.get('price'),p(d,'contact_name','contact_name_number'),p(d,'phone_numbers','all_contacts'),p(d,'captured_on','created_at'),d.get('verification'),d.get('verified_by'),'',d.get('source') or 'WHATSAPP']
def data(engine,k,q,limit):
 t,_,pk=SOURCES[k]
 with engine.connect() as c:
  total=int(c.execute(text(f'''SELECT COUNT(*) FROM "{t}" x WHERE NOT EXISTS(SELECT 1 FROM ai_source_record_archives a WHERE a.source_type=:s AND a.source_record_id=CAST(x."{pk}" AS text))'''),{'s':k}).scalar() or 0)
  rs=c.execute(text(f'''SELECT to_jsonb(x) FROM "{t}" x WHERE NOT EXISTS(SELECT 1 FROM ai_source_record_archives a WHERE a.source_type=:s AND a.source_record_id=CAST(x."{pk}" AS text)) AND (:q='' OR to_jsonb(x)::text ILIKE :pat) ORDER BY x."{pk}" DESC NULLS LAST LIMIT :lim'''),{'s':k,'q':q,'pat':'%'+q+'%','lim':limit}).scalars().all()
 return total,[r if isinstance(r,dict) else json.loads(r) for r in rs]
def media_count(engine,k,d):
 if k!='manual':return 0,0
 code=str(d.get('property_code') or ''); ph=vi=0
 if not code:return ph,vi
 with engine.connect() as c:
  for table,mcol in [('pi_operational_property_media','mime_type'),('pi_v14_property_media','content_type')]:
   if not c.execute(text('SELECT to_regclass(:n)'),{'n':'public.'+table}).scalar():continue
   sql=f'SELECT media_type,"{mcol}" FROM "{table}" WHERE property_code=:p' if table=='pi_operational_property_media' else f'SELECT NULL,"{mcol}" FROM "{table}" WHERE property_code=:p'
   for mt,mime in c.execute(text(sql),{'p':code}):
    if 'video' in ((mt or '')+' '+(mime or '')).lower():vi+=1
    else:ph+=1
 return ph,vi
def shell(body,k=''):
 tabs=''.join(f'<a class="{"on" if k==x else ""}" href="/alliance/source/{x}">{cfg[1]}</a>' for x,cfg in SOURCES.items())
 css='''*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#172033;font:12px Arial}header{background:#102a43;color:#fff;padding:14px 18px}nav,.tabs{background:#fff;border-bottom:1px solid #98a2b3;padding:7px;white-space:nowrap;overflow:auto}a{text-decoration:none}nav a,.tabs a,.btn,button{display:inline-block;background:#102a43;color:#fff;padding:7px 9px;margin:2px;border:0}.tabs a.on{background:#486581}.danger{background:#9b1c1c}.wrap{padding:10px}.kpi{display:inline-flex;flex-direction:column;background:#fff;border:1px solid #667085;padding:10px 16px}.kpi b{font-size:24px}.search{display:flex;gap:5px;margin:8px 0}.search input{min-width:320px}.tablebox{overflow:auto;max-height:73vh;border:1px solid #667085}table{border-collapse:collapse;width:max-content;min-width:100%;background:#fff}th,td{border:1px solid #98a2b3;padding:5px 6px;vertical-align:top;overflow-wrap:anywhere}th{background:#e9eef5;position:sticky;top:0;z-index:2}td.desc{min-width:360px;max-width:520px;white-space:pre-wrap}input,select,textarea{border:1px solid #98a2b3;padding:7px;width:100%}textarea{min-height:110px}.grid{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:8px;background:#fff;border:1px solid #98a2b3;padding:12px}.wide{grid-column:1/-1}.thumb{max-width:240px;max-height:180px;margin:6px;border:1px solid #98a2b3}@media(max-width:800px){.grid{grid-template-columns:1fr}}'''
 return f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body><header><b>Alliance CRE Intelligence OS 11.7</b><br>Full Property Database</header><nav><a href="/alliance/primary">Command Centre</a><a href="/fast-property-entry?division=DELHI_NCR">Add Property</a><a href="/commercial-intelligence">Commercial Intelligence</a></nav><div class="tabs">{tabs}</div><div class="wrap">{body}</div></body></html>'
def listing(engine,k,q,limit):
 total,rs=data(engine,k,q,limit); heads=['Property ID','Location','Description','Category','Type','Area','Unit','Floor','Rent/Sale Amount','Owner/Broker','Contact No.','Entry Date','Verification','Posted By','Assigned To','Source','Photos','Videos','Edit','Archive']; trs=[]
 for d in rs:
  n=norm(k,d);rid=str(d.get(SOURCES[k][2],''));ph,vi=media_count(engine,k,d);cells=[f'<td class="{"desc" if i==2 else ""}">{e(x)}</td>' for i,x in enumerate(n)];u=f'/alliance/property-media/{k}/{quote(rid,safe="")}'
  cells += [f'<td><a class="btn" href="{u}">Photos ({ph})</a></td>',f'<td><a class="btn" href="{u}">Videos ({vi})</a></td>',f'<td><a class="btn" href="/alliance/property-edit/{k}/{quote(rid,safe="")}">Edit</a></td>',f'<td><form method="post" action="/alliance/property-archive/{k}/{quote(rid,safe="")}" onsubmit="return confirm(\'Archive record?\')"><button class="danger">Archive</button></form></td>'];trs.append('<tr>'+''.join(cells)+'</tr>')
 body=f'<div class="kpi"><b>{total:,}</b><span>Active {SOURCES[k][1]} source records</span></div><form class="search"><input name="q" value="{e(q)}" placeholder="Search"><input type="hidden" name="limit" value="{limit}"><button>Search</button></form><div class="tablebox"><table><thead><tr>{"".join("<th>"+h+"</th>" for h in heads)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=20>No records</td></tr>"}</tbody></table></div>'
 return shell(body,k)
def one(engine,k,rid):
 t,_,pk=SOURCES[k]
 with engine.connect() as c:r=c.execute(text(f'SELECT to_jsonb(x) FROM "{t}" x WHERE CAST(x."{pk}" AS text)=:r'),{'r':rid}).scalar()
 if not r:raise HTTPException(404,'Record not found')
 return r if isinstance(r,dict) else json.loads(r)
def editform(engine,k,rid):
 n=norm(k,one(engine,k,rid)); names=['rid','location','description','category','ptype','area','unit','floor','amount','contact','phone','entry','verification','posted','assigned','source'];z=dict(zip(names,n));opts=''.join(f'<option {"selected" if str(z["unit"]).lower()==u.lower() else ""}>{u}</option>' for u in UNITS)
 body=f'''<h2>Edit {SOURCES[k][1]} Property</h2><form class="grid" method="post"><label>Location<input name="location" value="{e(z['location'])}"></label><label>Category<input name="category" value="{e(z['category'])}"></label><label>Type<input name="ptype" value="{e(z['ptype'])}"></label><label>Area Value<input name="area" value="{e(z['area'])}"></label><label>Area Unit<select name="unit">{opts}</select></label><label>Floor<input name="floor" value="{e(z['floor'])}"></label><label>Rent/Sale Amount<input name="amount" value="{e(z['amount'])}"></label><label>Owner/Broker<input name="contact" value="{e(z['contact'])}"></label><label>Contact No.<input name="phone" value="{e(z['phone'])}"></label><label>Verification<input name="verification" value="{e(z['verification'])}"></label><label>Posted By<input name="posted" value="{e(z['posted'])}"></label><label>Assigned To<input name="assigned" value="{e(z['assigned'])}"></label><label class="wide">Description<textarea name="description">{e(z['description'])}</textarea></label><div class="wide"><button>Save Changes</button> <a class="btn" href="/alliance/source/{k}">Cancel</a></div></form>'''
 return shell(body,k)
def save(engine,k,rid,f):
 pms=dict(f);pms['rid']=rid;pms['area_display']=(pms['area']+' '+pms['unit']).strip()
 if k=='manual':sql='''UPDATE pi_operational_properties SET location=:location,remarks=:description,transaction_type=:category,floor=:floor,owner_broker_name=:contact,contact_number=:phone,verification_status=:verification,entered_by=:posted,area_input=:area,area_unit=:unit,area_value=CASE WHEN :area ~ '^[0-9]+([.][0-9]+)?$' THEN CAST(:area AS numeric) ELSE area_value END,area_sqft=CASE WHEN :area ~ '^[0-9]+([.][0-9]+)?$' THEN CAST(:area AS numeric)*CASE :unit WHEN 'Sq Ft' THEN 1 WHEN 'Sq Yd' THEN 9 WHEN 'Sq Mtr' THEN 10.7639104167 WHEN 'Acre' THEN 43560 ELSE 1 END ELSE area_sqft END,updated_at=now() WHERE CAST(id AS text)=:rid'''
 elif k=='newspaper':sql='''UPDATE pi_newspaper_properties SET locality=:location,notes=:description,lead_type=:category,configuration_details=:ptype,area=:area_display,price=:amount,contact_person=:contact,phone_numbers=:phone,verification=:verification,team_member=:posted,updated_at=now() WHERE CAST(id AS text)=:rid'''
 elif k=='magazine':sql='''UPDATE pi_magazine_master SET locality=:location,original_raw_text=:description,category=:category,listing_type=:ptype,area=CASE WHEN :area ~ '^[0-9]+([.][0-9]+)?$' THEN CAST(:area AS numeric) ELSE area END,area_unit=:unit,floor=:floor,price=:amount,contact_name_company=:contact,record_status=:verification,updated_at=now() WHERE CAST(source_id AS text)=:rid'''
 else:sql='''UPDATE pi_whatsapp_property_master SET description=:description,lead_type=:category,configuration_details=:ptype,area=:area_display,price=:amount,contact_name=:contact,phone_numbers=:phone,verification=:verification,floor=:floor WHERE CAST(id AS text)=:rid'''
 with engine.begin() as c:c.execute(text(sql),pms)
def mediapage(engine,k,rid):
 d=one(engine,k,rid);items=[]
 if k=='manual':
  code=str(d.get('property_code') or '')
  if code:
   with engine.connect() as c:
    for table,store,mcol in [('pi_operational_property_media','operational','mime_type'),('pi_v14_property_media','v14','content_type')]:
     if not c.execute(text('SELECT to_regclass(:n)'),{'n':'public.'+table}).scalar():continue
     for mid,fn,mime in c.execute(text(f'SELECT id,filename,"{mcol}" FROM "{table}" WHERE property_code=:p ORDER BY id'),{'p':code}):items.append((store,mid,fn,mime))
 cards=[]
 for store,mid,fn,mime in items:
  u=f'/alliance/media-file/{store}/{mid}';cards.append((f'<video class="thumb" controls src="{u}"></video>' if 'video' in str(mime or '').lower() else f'<a target="_blank" href="{u}"><img class="thumb" src="{u}"></a>')+f'<br>{e(fn)}')
 return shell('<h2>Property Media</h2>'+(''.join(cards) if cards else '<p>No proven directly-linked media for this source record.</p>')+f'<p><a class="btn" href="/alliance/source/{k}">Back</a></p>',k)
def register(wrapped):
 app=wrapped.app;core=wrapped.core;engine=core.engine;ensure(engine)
 for k in SOURCES:
  path=f'/alliance/source/{k}';rm(app,path,{'GET'})
  def page(req:Request,q:str='',limit:int=200,_k=k):core.need_login(req);return HTMLResponse(listing(engine,_k,q.strip(),max(1,min(limit,500))),headers={'Cache-Control':'no-store','X-Alliance-CRE-Version':VERSION})
  app.add_api_route(path,page,methods=['GET'],include_in_schema=False)
 rm(app,'/alliance/final/database/manual',{'GET'})
 def manual(req:Request,q:str='',limit:int=200):core.need_login(req);return HTMLResponse(listing(engine,'manual',q.strip(),max(1,min(limit,500))))
 app.add_api_route('/alliance/final/database/manual',manual,methods=['GET'],include_in_schema=False)
 def eg(req:Request,source:str,record_id:str):core.need_login(req);return HTMLResponse(editform(engine,source,record_id)) if source in SOURCES else (_ for _ in ()).throw(HTTPException(404))
 async def ep(req:Request,source:str,record_id:str):
  core.need_login(req)
  if source not in SOURCES:raise HTTPException(404)
  fm=await req.form();f={x:str(fm.get(x,'')) for x in ('location','description','category','ptype','area','unit','floor','amount','contact','phone','verification','posted','assigned')};save(engine,source,record_id,f);return RedirectResponse(f'/alliance/source/{source}',303)
 app.add_api_route('/alliance/property-edit/{source}/{record_id}',eg,methods=['GET'],include_in_schema=False);app.add_api_route('/alliance/property-edit/{source}/{record_id}',ep,methods=['POST'],include_in_schema=False)
 def archive(req:Request,source:str,record_id:str):
  core.need_login(req)
  if source not in SOURCES:raise HTTPException(404)
  role=getattr(core,'get_role',lambda r:'team')(req) or 'team'
  with engine.begin() as c:c.execute(text('''INSERT INTO ai_source_record_archives(source_type,source_record_id,archived_by) VALUES(:s,:r,:b) ON CONFLICT(source_type,source_record_id) DO NOTHING'''),{'s':source,'r':record_id,'b':str(role)})
  return RedirectResponse(f'/alliance/source/{source}',303)
 app.add_api_route('/alliance/property-archive/{source}/{record_id}',archive,methods=['POST'],include_in_schema=False)
 def mp(req:Request,source:str,record_id:str):core.need_login(req);return HTMLResponse(mediapage(engine,source,record_id)) if source in SOURCES else (_ for _ in ()).throw(HTTPException(404))
 app.add_api_route('/alliance/property-media/{source}/{record_id}',mp,methods=['GET'],include_in_schema=False)
 def mf(req:Request,store:str,media_id:int):
  core.need_login(req);table,mcol=('pi_operational_property_media','mime_type') if store=='operational' else ('pi_v14_property_media','content_type') if store=='v14' else (None,None)
  if not table:raise HTTPException(404)
  with engine.connect() as c:r=c.execute(text(f'SELECT content,"{mcol}",filename FROM "{table}" WHERE id=:i'),{'i':media_id}).fetchone()
  if not r:raise HTTPException(404)
  return Response(bytes(r[0]),media_type=r[1] or 'application/octet-stream',headers={'Content-Disposition':f'inline; filename="{str(r[2] or "media").replace(chr(34),"")}"'})
 app.add_api_route('/alliance/media-file/{store}/{media_id}',mf,methods=['GET'],include_in_schema=False)
 preferred={f'/alliance/source/{k}' for k in SOURCES}|{'/alliance/final/database/manual','/alliance/property-edit/{source}/{record_id}','/alliance/property-archive/{source}/{record_id}','/alliance/property-media/{source}/{record_id}','/alliance/media-file/{store}/{media_id}'};chosen=[r for r in list(app.router.routes) if getattr(r,'path',None) in preferred]
 for r in chosen:
  try:app.router.routes.remove(r)
  except ValueError:pass
 for r in reversed(chosen):app.router.routes.insert(0,r)
 return {'status':'REGISTERED','version':VERSION}
