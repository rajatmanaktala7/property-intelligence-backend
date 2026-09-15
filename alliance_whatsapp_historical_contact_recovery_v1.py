from __future__ import annotations
import json, re
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

VERSION='1.0.0-EVIDENCE-ONLY-HISTORICAL-CONTACT-RECOVERY'
CONFIRM='APPLY_EVIDENCE_ONLY'
PHONE=re.compile(r'\D+')

def _phone(v):
    d=PHONE.sub('',str(v or ''))
    if d.startswith('00'): d=d[2:]
    if len(d)==12 and d.startswith('91'): d=d[-10:]
    if len(d)==11 and d.startswith('0'): d=d[-10:]
    return d if len(d)==10 and d[0] in '6789' else ''

def _walk(obj,path=''):
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f'{path}.{k}' if path else str(k)
            yield p,v
            yield from _walk(v,p)
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:100]): yield from _walk(v,f'{path}[{i}]')

def _evidence(payload):
    phones=set(); opaque=set(); samples=[]
    for path,value in _walk(payload):
        if isinstance(value,(dict,list)): continue
        low=path.lower(); raw=str(value or '').strip()
        if not raw: continue
        sender_key=any(k in low for k in ('sender','author','participant','phone_jid','sender_jid'))
        excluded=any(k in low for k in ('account_phone','group_phone','chat_phone'))
        if not sender_key or excluded: continue
        ph=_phone(raw)
        if ph: phones.add(ph)
        local=raw.split('@',1)[0]
        domain=raw.split('@',1)[1].lower() if '@' in raw else ''
        digits=PHONE.sub('',local)
        if domain=='lid' or (not ph and len(digits)>=13): opaque.add(local)
        if len(samples)<4: samples.append(path)
    if len(phones)==1 and len(opaque)<=1:
        return next(iter(phones)),next(iter(opaque),None),samples
    return None,None,samples

def _role(core,req):
    role=core.get_role(req) if callable(getattr(core,'get_role',None)) else None
    if role!='admin': raise HTTPException(403,'Admin required')

def _rows(engine,limit):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text('''
          SELECT id,event_id,entity_id,classification,sender_phone,payload_json
          FROM wa_bridge_events
          WHERE payload_json IS NOT NULL
          ORDER BY id ASC LIMIT :lim
        '''),{'lim':limit}).mappings()]

def plan(engine,limit=50000):
    rows=_rows(engine,limit); eligible=[]; ambiguous=0; no_evidence=0
    for row in rows:
        payload=row.get('payload_json') or {}
        if isinstance(payload,str):
            try: payload=json.loads(payload)
            except Exception: payload={}
        phone,opaque,paths=_evidence(payload)
        if not phone:
            ambiguous+=1 if paths else 0; no_evidence+=0 if paths else 1; continue
        eligible.append((row,phone,opaque,paths))
    return rows,eligible,ambiguous,no_evidence

def audit(engine,limit=50000):
    rows,eligible,ambiguous,no_evidence=plan(engine,limit)
    prop=req=event=0
    for row,phone,opaque,paths in eligible:
        entity=str(row.get('entity_id') or '')
        if entity.startswith('WAP-'): prop+=1
        elif entity.startswith('WAR-'): req+=1
        if not _phone(row.get('sender_phone')): event+=1
    return {'status':'READY','version':VERSION,'rows_scanned':len(rows),
      'unique_sender_phone_evidence':len(eligible),'ambiguous_identity_rows':ambiguous,
      'no_sender_phone_evidence':no_evidence,'event_updates_available':event,
      'property_updates_available':prop,'requirement_updates_available':req,
      'policy':'UNIQUE_SENDER_FIELDS_ONLY_NO_GUESSING','database_changed':False}

def apply(engine,limit=5000):
    rows,eligible,ambiguous,no_evidence=plan(engine,limit)
    counts={'events':0,'properties':0,'requirements':0,'registry':0}
    with engine.begin() as c:
        c.execute(text('''CREATE TABLE IF NOT EXISTS pi_whatsapp_contact_recovery_audit_v1(
          event_id TEXT PRIMARY KEY,entity_id TEXT,resolved_phone TEXT NOT NULL,
          evidence_paths JSONB NOT NULL,version TEXT NOT NULL,applied_at TIMESTAMPTZ DEFAULT NOW())'''))
        for row,phone,opaque,paths in eligible:
            eid=str(row.get('event_id') or row['id']); entity=str(row.get('entity_id') or '')
            ins=c.execute(text('''INSERT INTO pi_whatsapp_contact_recovery_audit_v1
              (event_id,entity_id,resolved_phone,evidence_paths,version)
              VALUES(:e,:x,:p,CAST(:j AS JSONB),:v) ON CONFLICT(event_id) DO NOTHING'''),
              {'e':eid,'x':entity or None,'p':phone,'j':json.dumps(paths),'v':VERSION}).rowcount
            if not ins: continue
            counts['events']+=c.execute(text('''UPDATE wa_bridge_events SET sender_phone=:p
              WHERE id=:id AND (sender_phone IS NULL OR BTRIM(sender_phone)='')'''),
              {'p':phone,'id':row['id']}).rowcount
            if entity.startswith('WAP-'):
                counts['properties']+=c.execute(text('''UPDATE wa_properties SET sender_phone=:p,updated_at=NOW()
                  WHERE wa_property_id=:id AND (sender_phone IS NULL OR BTRIM(sender_phone)='')'''),
                  {'p':phone,'id':entity}).rowcount
            elif entity.startswith('WAR-'):
                counts['requirements']+=c.execute(text('''UPDATE wa_requirements SET contact_phone=:p
                  WHERE wa_requirement_id=:id AND (contact_phone IS NULL OR BTRIM(contact_phone)='')'''),
                  {'p':phone,'id':entity}).rowcount
    return {'status':'APPLIED','version':VERSION,'counts':counts,'ambiguous_skipped':ambiguous,
      'no_evidence_skipped':no_evidence,'policy':'UNIQUE_SENDER_FIELDS_ONLY_NO_GUESSING'}

def register(core):
    app=getattr(core,'app',None) or core; router=APIRouter()
    import whatsapp_live_bridge as wb
    @router.get('/api/alliance/whatsapp-contact-recovery-v1/audit')
    def audit_route(req:Request,limit:int=Query(50000,ge=1,le=250000)):
        _role(core,req); return audit(wb.wa_engine,limit)
    @router.post('/api/alliance/whatsapp-contact-recovery-v1/apply')
    def apply_route(req:Request,confirm:str=Query(...),limit:int=Query(5000,ge=1,le=25000)):
        _role(core,req)
        if confirm!=CONFIRM: raise HTTPException(400,'Exact confirmation phrase required')
        return apply(wb.wa_engine,limit)
    app.include_router(router)
    return {'status':'REGISTERED','version':VERSION,'automatic_apply':False,'evidence_only':True}
