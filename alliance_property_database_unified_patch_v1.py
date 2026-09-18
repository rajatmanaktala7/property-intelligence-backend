from __future__ import annotations
import json,re
VERSION='1.1.0-COMPACT-UNIFIED-PROPERTY-DATABASES'
PHONE_RE=re.compile(r'(?<!\d)(?:\+?91[\s.()-]?)?([6-9](?:[\s.()-]?\d){9})(?!\d)')

def _phones(*values):
    out=[]
    def add(v):
        if v in (None,'',[],{}): return
        if isinstance(v,str):
            s=v.strip()
            if s[:1] in '[{':
                try: add(json.loads(s))
                except Exception: pass
            for m in PHONE_RE.finditer(s):
                p=re.sub(r'\D','',m.group(1))
                if len(p)==10 and p not in out: out.append(p)
            d=re.sub(r'\D','',s.replace('@s.whatsapp.net','').replace('@c.us',''))
            if len(d)==12 and d.startswith('91'): d=d[-10:]
            if len(d)==11 and d.startswith('0'): d=d[-10:]
            if len(d)==10 and d[0] in '6789' and d not in out: out.append(d)
        elif isinstance(v,dict):
            for x in v.values(): add(x)
        elif isinstance(v,(list,tuple,set)):
            for x in v: add(x)
        else: add(str(v))
    for v in values:add(v)
    return ', '.join(out)

def install():
    import alliance_final_5x5_databases_v910 as db
    if getattr(db,'_UNIFIED_PROPERTY_PATCH_V1',False):
        return {'status':'ALREADY_INSTALLED','version':VERSION}
    old_shell=db._shell
    old_rows=db._property_rows

    def shell(title,body):
        page=old_shell(title,body)
        if 'Property Database' not in str(title) and 'Property Databases' not in str(title): return page
        css="""<style id='property-unified-v1'>table{font-size:11px!important;font-weight:600;table-layout:auto}th,td{padding:4px 5px!important;line-height:1.18;max-width:190px;overflow-wrap:anywhere}th{white-space:nowrap}.propzoom{display:flex;align-items:center;gap:5px;margin:0 0 7px;background:#fff;padding:5px;border:1px solid #d0d5dd;width:max-content;position:sticky;left:0;z-index:6}.propzoom button{padding:4px 7px;font-weight:900}.desc{min-width:220px!important;max-width:320px!important}.loc{min-width:90px!important;max-width:130px!important}.nowrap{white-space:nowrap!important;max-width:150px!important}.tablebox{max-height:80vh!important}td:nth-child(7),td:nth-child(8){max-width:125px!important}td:nth-child(11),td:nth-child(12),td:nth-child(13){max-width:120px!important}td:nth-child(14),td:nth-child(15),td:nth-child(16){max-width:140px!important}</style>"""
        js="""<script id='property-zoom-v1'>(function(){let z=Number(localStorage.getItem('alliancePropertyZoom')||100);function a(){document.querySelectorAll('.tablebox table').forEach(t=>t.style.fontSize=(12*z/100)+'px');document.querySelectorAll('.tablebox th,.tablebox td').forEach(x=>x.style.padding=(7*z/100)+'px '+(8*z/100)+'px')}window.propZoom=function(d){z=Math.max(70,Math.min(170,z+d*10));localStorage.setItem('alliancePropertyZoom',z);a()};window.propZoomReset=function(){z=100;localStorage.setItem('alliancePropertyZoom',z);a()};a()})();</script>"""
        controls="<div class='propzoom'><b>Table Zoom</b><button type='button' onclick='propZoom(-1)'>−</button><button type='button' onclick='propZoom(1)'>+</button><button type='button' onclick='propZoomReset()'>Reset</button></div>"
        page=page.replace('</head>',css+'</head>')
        if '<div class="tablebox">' in page: page=page.replace('<div class="tablebox">',controls+'<div class="tablebox">',1)
        return page.replace('</body>',js+'</body>')

    def property_table(core,e,req,source,q,location,category,transaction,status,assigned,limit):
        rows=old_rows(e,source,q,location,category,transaction,status,assigned,limit)
        trs=[]
        for r in rows:
            cr=db._dict(r.get('clean_record')); cid=str(r.get('canonical_id') or '')
            locality=r.get('locality') or db._first(cr,'location','locality') or ''
            address=db._first(cr,'address','exact_address','property_address') or ''
            desc=db._first(cr,'team_description','description_edit','description','original_description','original_message','raw_line','source_text') or ''
            if address and address.lower() not in str(desc).lower(): desc=(address+' · '+str(desc)).strip(' ·')
            tx=r.get('transaction_type') or db._first(cr,'transaction_type','rent_or_sale') or ''
            pcat=db._property_category(cr,tx)
            ptype=db._first(cr,'property_type','asset_type','subtype') or ''
            area=db._first(cr,'area_display','area','available_area')
            if not area:
                av=db._first(cr,'area_value') or r.get('area_value') or r.get('area_sqft') or ''
                au=db._first(cr,'area_unit') or r.get('area_unit') or ('SQFT' if r.get('area_sqft') else '')
                area=f'{av} {au}'.strip()
            floor=db._first(cr,'floor','floors','floor_codes') or ''
            if isinstance(floor,list): floor=', '.join(map(str,floor))
            amount=db._first(cr,'rent','monthly_rent','rent_amount','rent_in_figures') if str(tx).upper() in ('RENT','LEASE') else db._first(cr,'sale_price','sale_amount','price','asking_price')
            amount=amount or db._first(cr,'amount','price_raw') or r.get('price_raw') or ''
            cname,cphone=db._contacts(cr)
            # Master properties already carry normalized phones separately from clean_record.
            # Use them before declaring the contact missing. This is especially important
            # for WhatsApp records where sender/advertiser numbers are stored in p.phones.
            if not cphone: cphone=_phones(r.get('phones'),cr.get('phone_numbers'),cr.get('phones'),cr.get('sender_phone'),cr.get('sender_jid'),cr.get('contact_phone'),cr.get('contact_number'))
            stat=r.get('availability_status')
            if not stat or stat=='UNKNOWN': stat=r.get('verification_status') or 'UNVERIFIED'
            # Avoid one extra DB connection/query per displayed row. Source pages already
            # come from canonical source filtering; Master falls back to stored lineage hints.
            source_name=source if source!='MASTER' else (db._first(cr,'source','source_type','source_table') or r.get('source_version') or 'MASTER')
            verify=f'''<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/primary/property/{db._e(cid)}/verify"><select name="status" required><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>CALL_BACK</option><option>SOLD</option><option>RENTED</option><option>HOLD</option><option>WRONG_NUMBER</option></select><select name="verified_with" required><option>OWNER</option><option>BROKER</option><option>OTHER</option></select><input name="verified_by" required placeholder="Verified By team member"><input name="remarks" placeholder="Remarks"><input type="datetime-local" name="next_verification_at"><button class="good">Save</button></form></div></details>'''
            delete=f'''<form method="post" action="/alliance/primary/property/{db._e(cid)}/delete" onsubmit="return confirm('Archive this property? Original source evidence remains preserved.');"><button class="danger">Delete</button></form>'''
            history=f'<a class="btn light" href="/alliance/primary/property/{db._e(cid)}">History</a>'
            edit=f'<a class="btn light" href="/alliance/primary/property/{db._e(cid)}/edit">Edit</a>'
            vals=[db._fmt_dt(r.get('created_at')),desc,locality,pcat,tx,amount,cname,cphone,stat,verify,ptype,area,floor,r.get('assigned_to') or '',source_name,cid,history,edit,delete]
            raw={9,16,17,18}
            cls=['nowrap','desc','loc','','nowrap','','','','nowrap','','','','','','','nowrap','','','']
            trs.append('<tr>'+''.join(f'<td class="{cls[i]}">{v if i in raw else db._e(db._shown(v))}</td>' for i,v in enumerate(vals))+'</tr>')
        H=['Date & Time','Description / Address','Location','Property Category','Rent / Sale','Amount','Contact Name','Contact No.','Status','Verify','Property Type','Area','Floor','Assigned To','Source','Property ID','History','Edit','Delete']
        table=''.join(trs) if trs else '<tr><td colspan="19">No records found</td></tr>'
        return db._filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{table}</tbody></table></div>'

    db._shell=shell
    db._property_table=property_table
    db._UNIFIED_PROPERTY_PATCH_V1=True
    return {'status':'INSTALLED','version':VERSION,'same_renderer_all_sources':True,'zoom':'70-170%','whatsapp_phone_source':'MASTER_PHONES_PLUS_CLEAN_RECORD','n_plus_one_source_queries_removed':True,'database_changed':False}
