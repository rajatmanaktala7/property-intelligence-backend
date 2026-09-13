from __future__ import annotations
import re, json
from collections import defaultdict
from sqlalchemy import text

VERSION='1.1.0-SQLALCHEMY-INSPECTOR-DISCOVERY'
REGISTRY_TABLE='pi_whatsapp_sender_identity_registry_v1'
PHONE_COLS=('sender_phone','phone_number','sender_number','author_phone','contact_phone','mobile','phone','whatsapp_phone')
ID_COLS=('sender_jid','jid','author','participant','remote_jid','sender_id','lid','participant_id','author_id')
NAME_COLS=('sender_name','sender_display_name','author_name','sender')

def _norm(v): return str(v or '').strip()
def _phone(v):
    d=re.sub(r'\D+','',_norm(v))
    if d.startswith('00'): d=d[2:]
    if len(d)==12 and d.startswith('91'): d=d[-10:]
    if len(d)==11 and d.startswith('0'): d=d[-10:]
    return d if len(d)==10 and d[0] in '6789' else ''

def _opaque(v):
    s=_norm(v)
    if not s: return ''
    local=s.split('@',1)[0]
    domain=s.split('@',1)[1].lower() if '@' in s else ''
    digits=re.sub(r'\D+','',local)
    if domain in ('s.whatsapp.net','c.us'): return ''
    if domain=='lid': return local
    return digits if len(digits)>=13 else ''

def _inspector(engine):
    from sqlalchemy import inspect
    return inspect(engine)

def _cols(engine,table):
    ins=_inspector(engine)
    return {str(c["name"]) for c in ins.get_columns(table)}

def _tables(engine):
    ins=_inspector(engine)
    names=set(ins.get_table_names())
    try:
        names.update(ins.get_view_names())
    except Exception:
        pass
    return sorted(
        str(n) for n in names
        if str(n).lower().startswith(("wa_","wai_","pi_whatsapp"))
    )

def ensure_registry(engine):
    ddl=(f"CREATE TABLE IF NOT EXISTS {REGISTRY_TABLE}("
         "opaque_id TEXT PRIMARY KEY,resolved_phone TEXT,resolution_status TEXT NOT NULL,"
         "confidence INTEGER NOT NULL DEFAULT 0,evidence_count INTEGER NOT NULL DEFAULT 0,"
         "conflicting_phones INTEGER NOT NULL DEFAULT 0,source_tables TEXT,evidence_json TEXT,"
         "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
    with engine.begin() as c: c.execute(text(ddl))

def _rows(engine,table,limit=250000):
    cols=_cols(engine,table)
    idc=next((x for x in ID_COLS if x in cols),None)
    phc=next((x for x in PHONE_COLS if x in cols),None)
    namec=next((x for x in NAME_COLS if x in cols),None)
    if not idc: return []
    sel=[f'"{idc}" AS opaque_raw', f'"{phc}" AS phone_raw' if phc else 'NULL AS phone_raw',
         f'"{namec}" AS name_raw' if namec else 'NULL AS name_raw']
    q=text(f'SELECT {",".join(sel)} FROM "{table}" WHERE "{idc}" IS NOT NULL LIMIT :lim')
    with engine.connect() as c:
        return [dict(r) for r in c.execute(q,{'lim':limit}).mappings()]

def build_plan(engine):
    ev=defaultdict(lambda:{'phones':defaultdict(int),'tables':set(),'samples':[]})
    scanned=0; tables=_tables(engine)
    for table in tables:
        try: rows=_rows(engine,table)
        except Exception: continue
        scanned+=len(rows)
        for r in rows:
            oid=_opaque(r.get('opaque_raw'))
            if not oid: continue
            p=_phone(r.get('phone_raw')) or _phone(r.get('name_raw'))
            if not p: continue
            e=ev[oid]; e['phones'][p]+=1; e['tables'].add(table)
            if len(e['samples'])<5: e['samples'].append({'table':table,'phone':p,'opaque':_norm(r.get('opaque_raw'))})
    plan=[]
    for oid,e in ev.items():
        pc=dict(e['phones']); total=sum(pc.values())
        if len(pc)==1:
            phone=next(iter(pc)); status='RESOLVED_UNIQUE_EXACT_EVIDENCE'; confidence=100; conflicts=0
        else:
            phone=None; status='AMBIGUOUS_CONFLICT'; confidence=0; conflicts=len(pc)
        plan.append({'opaque_id':oid,'resolved_phone':phone,'resolution_status':status,'confidence':confidence,
                     'evidence_count':total,'conflicting_phones':conflicts,'source_tables':sorted(e['tables']),
                     'evidence_json':{'phones':pc,'samples':e['samples']}})
    return {'version':VERSION,'tables_scanned':len(tables),'tables_discovered':tables,
            'rows_scanned':scanned,
            'resolved_unique':sum(1 for x in plan if x['resolution_status']=='RESOLVED_UNIQUE_EXACT_EVIDENCE'),
            'ambiguous':sum(1 for x in plan if x['resolution_status']=='AMBIGUOUS_CONFLICT'),'plan':plan}

def apply_registry(engine):
    ensure_registry(engine); rep=build_plan(engine)
    sql=text(f"INSERT INTO {REGISTRY_TABLE} (opaque_id,resolved_phone,resolution_status,confidence,evidence_count,conflicting_phones,source_tables,evidence_json,updated_at) VALUES(:opaque_id,:resolved_phone,:resolution_status,:confidence,:evidence_count,:conflicting_phones,:source_tables,:evidence_json,NOW()) ON CONFLICT (opaque_id) DO UPDATE SET resolved_phone=EXCLUDED.resolved_phone,resolution_status=EXCLUDED.resolution_status,confidence=EXCLUDED.confidence,evidence_count=EXCLUDED.evidence_count,conflicting_phones=EXCLUDED.conflicting_phones,source_tables=EXCLUDED.source_tables,evidence_json=EXCLUDED.evidence_json,updated_at=NOW()")
    with engine.begin() as c:
        for x in rep['plan']:
            p=dict(x); p['source_tables']=json.dumps(x['source_tables']); p['evidence_json']=json.dumps(x['evidence_json'])
            c.execute(sql,p)
    return rep

def resolve_many(engine,ids):
    ids=sorted({_opaque(x) for x in ids if _opaque(x)})
    if not ids: return {}
    ensure_registry(engine); out={}
    with engine.connect() as c:
        for i in range(0,len(ids),400):
            chunk=ids[i:i+400]; params={f'x{j}':v for j,v in enumerate(chunk)}
            holders=','.join(':'+k for k in params)
            q=text(f'SELECT opaque_id,resolved_phone,resolution_status FROM {REGISTRY_TABLE} WHERE opaque_id IN ({holders})')
            for r in c.execute(q,params).mappings():
                if r['resolution_status']=='RESOLVED_UNIQUE_EXACT_EVIDENCE' and _phone(r['resolved_phone']):
                    out[str(r['opaque_id'])]=_phone(r['resolved_phone'])
    return out
