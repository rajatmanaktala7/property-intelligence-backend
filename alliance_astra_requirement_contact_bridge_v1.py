from __future__ import annotations
import re
from sqlalchemy import text
VERSION="1.0.0-ASTRA-WHATSAPP-CONTACT-BRIDGE"

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
    # Astra's proven difference: normalize numeric WhatsApp JIDs/91-prefixed sender
    # identities before rejecting them as non-phone values.
    fix._phones=_deep_phones
    old_lookup=fix._wa_lookup
    old_norm=fix._norm_req
    old_page=fix._requirements_page

    def astra_evidence(source_table,source_id):
        if not source_id:return ''
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

    def wa_lookup(row):
        p=_deep_phones(row)
        if p:return p
        p=old_lookup(row)
        if p:return p
        sid=fix._first(row,['source_pk','wa_requirement_id','requirement_id','record_id','id'])
        stable=fix._first(row,['source_table']) or 'WHATSAPP'
        return astra_evidence(stable,sid)
    fix._wa_lookup=wa_lookup

    def norm(obj,source_table=''):
        r=old_norm(obj,source_table)
        if str(r.get('source') or '').upper()=='WHATSAPP' and str(r.get('contact') or '') in ('','Not captured'):
            sid=r.get('source_id')
            p=astra_evidence(source_table,sid)
            if not p:
                try:p=wa_lookup(obj if isinstance(obj,dict) else {})
                except Exception:p=''
            if p:r['contact']=p
        # One compact client/contact field requested by user.
        bits=[]
        for v in (r.get('company'),r.get('name'),r.get('contact')):
            s=str(v or '').strip()
            if s and s!='Not captured' and s not in bits:bits.append(s)
        r['company']=' · '.join(bits) if bits else 'Not captured'
        r['name']=''
        r['contact']=''
        return r
    fix._norm_req=norm

    def requirements_page(engine_obj,source,q=''):
        page=old_page(engine_obj,source,q)
        page=page.replace('Client / Company</th><th>Contact Name</th><th>Contact No.</th>','Client / Contact</th><th class="mergehide">Contact Name</th><th class="mergehide">Contact No.</th>')
        page=page.replace('</style>','th:nth-child(4),td:nth-child(4),th:nth-child(5),td:nth-child(5){display:none}.desc{min-width:210px;max-width:330px}.loc{min-width:70px;max-width:120px}table{font-size:9px}th,td{padding:3px 4px}</style>')
        return page
    fix._requirements_page=requirements_page
    return {'status':'INSTALLED','version':VERSION,'contact':'ASTRA_JID_PLUS_EVIDENCE_LINEAGE','client_contact':'MERGED','compact':True}
