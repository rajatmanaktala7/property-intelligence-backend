from __future__ import annotations
import hashlib,json,re
from fastapi import APIRouter,HTTPException,Query,Request
from sqlalchemy import text

VERSION='1.1.0-STRICT-DEMAND-SUPPLY-TRUTH-AUDIT'
# A requirement needs explicit buyer, tenant, brand, or client intent.
STRONG_DEMAND=re.compile(
    r'(?:^|\b)(?:need|needed|wanted|looking\s+for|'
    r'(?:client|buyer|tenant|brand|company)\s+'
    r'(?:needs|requires|required|is\s+looking\s+for)|'
    r'want\s+to\s+(?:buy|rent|lease))\b',
    re.I,
)

STRONG_SUPPLY=re.compile(
    r'(?:^|\b)(?:for\s+sale|for\s+rent|to\s+let|'
    r'available\s+for|property\s+available|'
    r'rented\s+.+\s+for\s+sale|'
    r'owner\s+listing|broker\s+listing|'
    r'fetching\s+(?:the\s+)?rent|'
    r'please\s+contact)\b',
    re.I,
)

PRICE_OFFER=re.compile(
    r'(?:₹|rs\.?|inr)\s*[\d,.]+|'
    r'\b\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh|k)\b',
    re.I,
)

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
    value=str(message or '').strip()
    demand=bool(STRONG_DEMAND.search(value))
    supply=bool(STRONG_SUPPLY.search(value))
    price_offer=bool(PRICE_OFFER.search(value))

    # Explicit supply language wins unless a separate, explicit client,
    # buyer, tenant, or brand requirement is present.
    if supply and not demand:
        return 'LIKELY_PROPERTY_SUPPLY'

    if demand and not supply:
        return 'LIKELY_REQUIREMENT'

    if demand and supply:
        return 'NEEDS_HUMAN_REVIEW'

    if price_offer:
        return 'LIKELY_PROPERTY_SUPPLY'

    return 'NEEDS_HUMAN_REVIEW'

def audit(engine,per_table=1000):
    with engine.connect() as c:
        discovered=[str(x) for x in c.execute(text('''
          SELECT table_name FROM information_schema.tables
          WHERE table_schema=current_schema() AND table_type='BASE TABLE'
            AND (table_name ILIKE '%newspaper%' OR table_name ILIKE '%magazine%')
          ORDER BY table_name
        ''')).scalars()]

        # Only canonical business-data tables are eligible for this audit.
        # Audit, run, history, evidence, lesson and certification tables are
        # operational metadata and must not be counted as business records.
        authoritative_tables={
            'pi_newspaper_properties',
            'pi_whatsapp_newspaper_format',
            'pi_magazine_master',
        }
        tables=[
            name for name in discovered
            if name in authoritative_tables
        ]
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
