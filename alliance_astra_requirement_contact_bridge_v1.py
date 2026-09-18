from __future__ import annotations
import re
from sqlalchemy import text
VERSION="1.2.0-WHATSAPP-REQUIREMENT-CONTACT-BRIDGE"

def _phone(v):
    s=str(v or '').strip().replace('@s.whatsapp.net','').replace('@c.us','')
    d=re.sub(r'\D+','',s)
    if d.startswith('00'): d=d[2:]
    if len(d)==12 and d.startswith('91'): d=d[-10:]
    if len(d)==11 and d.startswith('0'): d=d[-10:]
    return d if len(d)==10 and d[0] in '6789' else ''

def _deep_phones(*values):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            for x in v.values(): walk(x)
        elif isinstance(v,(list,tuple,set)):
            for x in v: walk(x)
        else:
            p=_phone(v)
            if p and p not in out: out.append(p)
            for m in re.finditer(r'(?<!\d)(?:\+?91[\s.()-]?)?([6-9](?:[\s.()-]?\d){9})(?!\d)',str(v or '')):
                p2=re.sub(r'\D','',m.group(1))
                if len(p2)==10 and p2 not in out: out.append(p2)
    for v in values: walk(v)
    return ', '.join(out)

def install(fix, engine):
    old_lookup=getattr(fix,'_wa_lookup',None) or getattr(fix,'_sender_from_whatsapp',None)
    if not callable(old_lookup):
        def old_lookup(row): return ''
    old_norm=getattr(fix,'_norm_req')
    old_page=getattr(fix,'_requirements_page')
    fix._phones=_deep_phones

    def astra_evidence(source_table,source_id):
        if not source_id or engine is None:return ''
        try:
            with engine.connect() as c:
                rows=c.execute(text("""SELECT c.whatsapp_phone,c.phone,e.evidence_json
                  FROM pi_clean_contact_evidence_v1 e
                  JOIN pi_clean_contacts_v1 c ON c.canonical_key=e.canonical_key
                  WHERE CAST(e.source_record_id AS TEXT)=:sid
                    AND (UPPER(COALESCE(e.source_type,''))='WHATSAPP' OR LOWER(COALESCE(e.source_table,'')) LIKE '%whatsapp%' OR LOWER(COALESCE(e.source_table,'')) LIKE 'wa_%')
                  ORDER BY e.created_at DESC LIMIT 20"""),{'sid':str(source_id)}).mappings().all()
            for r in rows:
                p=_deep_phones(r.get('whatsapp_phone'),r.get('phone'),r.get('evidence_json'))
                if p:return p
        except Exception:pass
        return ''

    def restored_requirement_phone(source_id):
        # Availability/WhatsApp recovery writes the verified sender number into
        # wa_requirements.contact_phone. Requirement pages must read that same
        # restored authority instead of showing "Not captured".
        sid=str(source_id or '').strip()
        if not sid:return ''
        try:
            import whatsapp_live_bridge as wb
            wa_engine=getattr(wb,'wa_engine',None)
            if wa_engine is None:return ''
            with wa_engine.connect() as c:
                r=c.execute(text("""SELECT contact_phone FROM wa_requirements
                  WHERE wa_requirement_id=:sid LIMIT 1"""),{'sid':sid}).mappings().first()
            return _deep_phones(r.get('contact_phone')) if r else ''
        except Exception:return ''

    def wa_lookup(row):
        p=_deep_phones(row)
        if p:return p
        try:p=old_lookup(row)
        except Exception:p=''
        if p:return p
        sid=fix._first(row,['source_pk','wa_requirement_id','requirement_id','record_id','id'])
        p=restored_requirement_phone(sid)
        if p:return p
        stable=fix._first(row,['source_table']) or 'WHATSAPP'
        return astra_evidence(stable,sid)
    fix._wa_lookup=wa_lookup
    fix._sender_from_whatsapp=wa_lookup

    # Keep Company, Contact Name and Contact No. as independent structured fields.
    # Only enrich a missing WhatsApp number from evidence lineage.
    def norm(obj,source_table=''):
        r=old_norm(obj,source_table)
        if str(r.get('source') or '').upper()=='WHATSAPP' and str(r.get('contact') or '') in ('','Not captured'):
            sid=r.get('source_id')
            p=restored_requirement_phone(sid)
            if not p:p=astra_evidence(source_table,sid)
            if not p:
                try:p=wa_lookup(obj if isinstance(obj,dict) else {})
                except Exception:p=''
            if p:r['contact']=p
        return r
    fix._norm_req=norm

    def requirements_page(engine_obj,source,q=''):
        page=old_page(engine_obj,source,q)
        # Current fast table has 17 columns. Put sparse fields after Run Matcher.
        order=[0,1,2,3,4,5,6,10,11,16,7,8,9,12,13,14,15]
        headers=['Date / Time','Requirement / Description','Client / Company','Contact Name','Contact No.','Location','Category / Purpose','Rent / Sale','Budget','Run Matcher','Property Type','Area Min','Area Max','Floor / Preference','Verification','Source','Source ID']
        page=re.sub(r'<thead><tr>.*?</tr></thead>',"<thead><tr>"+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead>',page,count=1,flags=re.S)
        def reorder_row(m):
            attrs=m.group(1); inner=m.group(2)
            cells=re.findall(r'<td(?:\s[^>]*)?>.*?</td>',inner,flags=re.S)
            if len(cells)!=17:return m.group(0)
            return '<tr'+attrs+'>'+''.join(cells[i] for i in order)+'</tr>'
        page=re.sub(r'<tr([^>]*)>(.*?)</tr>',reorder_row,page,flags=re.S)
        css="""<style id='requirement-readable-v2'>table{font-size:12px!important}th,td{padding:7px 8px!important;line-height:1.3}.reqzoom{display:flex;align-items:center;gap:6px;margin:0 0 8px 0;width:max-content;background:white;border:1px solid #98a2b3;border-radius:6px;padding:5px}.reqzoom button{font-size:13px;padding:5px 9px}.desc{min-width:300px!important;max-width:500px!important}</style>"""
        controls="<div class='reqzoom'><b>Table Zoom</b><button type='button' onclick='reqZoom(-10)'>−</button><button type='button' onclick='reqZoom(10)'>+</button><button type='button' onclick='reqZoomReset()'>Reset</button><span id='reqZoomValue'>100%</span></div>"
        js="""<script id='requirement-zoom-v2'>(function(){var z=parseInt(localStorage.getItem('allianceReqZoom')||'100',10);function apply(){z=Math.max(70,Math.min(170,z));document.querySelectorAll('.tablebox table').forEach(function(t){t.style.fontSize=(12*z/100)+'px'});var v=document.getElementById('reqZoomValue');if(v)v.textContent=z+'%';localStorage.setItem('allianceReqZoom',String(z));}window.reqZoom=function(d){z+=d;apply()};window.reqZoomReset=function(){z=100;apply()};apply();})();</script>"""
        page=page.replace('</head>',css+'</head>',1)
        page=page.replace('<div class=tablebox>',controls+'<div class=tablebox>',1)
        page=page.replace('</body>',js+'</body>',1)
        return page
    fix._requirements_page=requirements_page
    return {'status':'INSTALLED','version':VERSION,'contact':'SEPARATE_WITH_ASTRA_EVIDENCE_RECOVERY','client_contact':'SEPARATE','zoom':'70-170%','priority_order':'MATCHER_BEFORE_SPARSE_FIELDS','database_changed':False}
