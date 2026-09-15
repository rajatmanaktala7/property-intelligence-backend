from __future__ import annotations
import hashlib,json,re
from fastapi import APIRouter,HTTPException,Query,Request
from sqlalchemy import text

VERSION='1.0.0-NEWSPAPER-MAGAZINE-REQUIREMENT-TRUTH-AUDIT'
DEMAND=re.compile(r'\b(need|needed|require|requires|required|requirement|wanted|looking\s+for|client\s+(?:needs|requires|looking)|want\s+to\s+(?:buy|rent|lease))\b',re.I)
SUPPLY=re.compile(r'\b(available|availability|for\s+sale|for\s+rent|to\s+let|selling|renting|property\s+available|owner|broker)\b',re.I)

def _role(core,req):
    role=core.get_role(req) if callable(getattr(core,'get_role',None)) else None
    if role!='admin': raise HTTPException(403,'Admin required')

def _norm(v): return re.sub(r'\s+',' ',str(v or '')).strip().lower()
def _message(obj):
    for key in ('original_message','raw_message','raw_text','message','description','requirement_text','text','content'):
        value=obj.get(key)
        if value and len(str(value).strip())>=8: return str(value).strip()
    return ''

def _classify(message):
    demand=bool(DEMAND.search(message or '')); supply=bool(SUPPLY.search(message or ''))
    if demand: return 'LIKELY_REQUIREMENT'
    if supply: return 'LIKELY_PROPERTY_SUPPLY'
    return 'NEEDS_HUMAN_REVIEW'

def audit(engine,per_table=1000):
    with engine.connect() as c:
        tables=[str(x) for x in c.execute(text('''
          SELECT table_name FROM information_schema.tables
          WHERE table_schema=current_schema() AND table_type='BASE TABLE'
            AND (table_name ILIKE '%newspaper%' OR table_name ILIKE '%magazine%')
          ORDER BY table_name
        ''')).scalars()]
        existing=set()
        try:
            for value in c.execute(text("SELECT original_message FROM pi_requirement_gate_v1191 WHERE original_message IS NOT NULL")).scalars():
                n=_norm(value)
                if n: existing.add(hashlib.sha256(n.encode()).hexdigest())
        except Exception: pass

    totals={'LIKELY_REQUIREMENT':0,'LIKELY_PROPERTY_SUPPLY':0,'NEEDS_HUMAN_REVIEW':0,'ALREADY_IN_GATE':0}
    reports=[]; errors={}
    for table in tables:
        try:
            safe='"'+table.replace('"','""')+'"'
            with engine.connect() as c:
                total=int(c.execute(text(f'SELECT COUNT(*) FROM {safe}')).scalar() or 0)
                rows=c.execute(text(f'SELECT to_jsonb(t) FROM {safe} t LIMIT :n'),{'n':per_table}).scalars().all()
            counts={'LIKELY_REQUIREMENT':0,'LIKELY_PROPERTY_SUPPLY':0,'NEEDS_HUMAN_REVIEW':0,'ALREADY_IN_GATE':0}
            samples=[]
            for raw in rows:
                obj=dict(raw) if isinstance(raw,dict) else {}
                msg=_message(obj)
                cls=_classify(msg)
                if msg and hashlib.sha256(_norm(msg).encode()).hexdigest() in existing: cls='ALREADY_IN_GATE'
                counts[cls]+=1; totals[cls]+=1
                if cls=='LIKELY_REQUIREMENT' and len(samples)<3:
                    samples.append({'text':msg[:220],'source_pk':str(obj.get('id') or obj.get('record_id') or '')})
            source='NEWSPAPER' if 'newspaper' in table.lower() else 'MAGAZINE'
            reports.append({'source':source,'table':table,'total_rows':total,'rows_scanned':len(rows),'classification':counts,'candidate_samples':samples})
        except Exception as exc: errors[table]=f'{type(exc).__name__}: {str(exc)[:180]}'
    return {'status':'READY','version':VERSION,'tables_discovered':len(tables),'tables':reports,'totals':totals,
      'errors':errors,'policy':'AUDIT_ONLY_NO_AUTOMATIC_PROMOTION','database_changed':False}

def register(core):
    app=getattr(core,'app',None) or core; router=APIRouter()
    @router.get('/api/alliance/requirement-source-truth-v1/audit')
    def route(req:Request,per_table:int=Query(1000,ge=10,le=5000)):
        _role(core,req); return audit(core.engine,per_table)
    app.include_router(router)
    return {'status':'REGISTERED','version':VERSION,'automatic_promotion':False}
