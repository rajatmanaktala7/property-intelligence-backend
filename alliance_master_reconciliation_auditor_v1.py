from __future__ import annotations
from fastapi import APIRouter,HTTPException,Request
from sqlalchemy import inspect,text

VERSION='1.0.0-MASTER-REQUIREMENT-PROPERTY-LINEAGE-AUDIT'
SKIP=('audit','run','history','log','match','lesson','exam','job','queue','backup','snapshot','migration','schema','duplicate','evidence','certification','training','staging','stage','map','link','workflow','action','counter','cursor')

def _role(core,req):
    role=core.get_role(req) if callable(getattr(core,'get_role',None)) else None
    if role!='admin': raise HTTPException(403,'Admin required')

def _exists(engine,name):
    try: return bool(inspect(engine).has_table(name))
    except Exception: return False

def _count(c,table,where=''):
    safe='"'+table.replace('"','""')+'"'
    return int(c.execute(text(f'SELECT COUNT(*) FROM {safe} {where}')).scalar() or 0)

def _source_report(engine,names,entity):
    token='requirement' if entity=='REQUIREMENT' else 'property'
    masters={'pi_requirement_gate_v1191','pi_master_requirements_v711'} if entity=='REQUIREMENT' else {'pi_master_properties_v711'}
    candidates=[]
    for name in names:
        low=name.lower()
        if token not in low or name in masters or any(x in low for x in SKIP): continue
        try:
            cols={str(x['name']) for x in inspect(engine).get_columns(name)}
            id_col=next((x for x in ('id','record_id','requirement_id','wa_requirement_id','property_id','wa_property_id','canonical_id') if x in cols),None)
            with engine.connect() as c:
                total=_count(c,name)
                linked=0
                if _exists(engine,'pi_master_source_links_v711'):
                    linked=int(c.execute(text('''SELECT COUNT(DISTINCT source_pk)
                      FROM pi_master_source_links_v711
                      WHERE master_entity_type=:e AND source_table=:t'''),{'e':entity,'t':name}).scalar() or 0)
            candidates.append({'table':name,'rows':total,'linked_source_rows':linked,
              'unlinked_estimate':max(total-linked,0),'id_column':id_col,
              'manual_source':('manual' in low or 'form' in low),'columns':len(cols)})
        except Exception as exc:
            candidates.append({'table':name,'error':f'{type(exc).__name__}: {str(exc)[:160]}'})
    candidates.sort(key=lambda x:(not x.get('manual_source',False),-int(x.get('rows',0)),x['table']))
    return candidates

def audit(engine):
    names=sorted(str(x) for x in inspect(engine).get_table_names())
    with engine.connect() as c:
        gate=_count(c,'pi_requirement_gate_v1191') if _exists(engine,'pi_requirement_gate_v1191') else 0
        gate_active=int(c.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE')")).scalar() or 0) if gate else 0
        gate_match=int(c.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE matcher_eligible=TRUE")).scalar() or 0) if gate else 0
        master_req=_count(c,'pi_master_requirements_v711') if _exists(engine,'pi_master_requirements_v711') else 0
        master_prop=_count(c,'pi_master_properties_v711') if _exists(engine,'pi_master_properties_v711') else 0
        links_req=int(c.execute(text("SELECT COUNT(DISTINCT canonical_id) FROM pi_master_source_links_v711 WHERE master_entity_type='REQUIREMENT'")).scalar() or 0) if _exists(engine,'pi_master_source_links_v711') else 0
        links_prop=int(c.execute(text("SELECT COUNT(DISTINCT canonical_id) FROM pi_master_source_links_v711 WHERE master_entity_type='PROPERTY'")).scalar() or 0) if _exists(engine,'pi_master_source_links_v711') else 0
        availability={}
        if _exists(engine,'pi_master_workflow_v720'):
            for r in c.execute(text("SELECT COALESCE(availability_status,'UNKNOWN') s,COUNT(*) n FROM pi_master_workflow_v720 WHERE entity_type='PROPERTY' GROUP BY 1")).mappings(): availability[str(r['s'])]=int(r['n'])
    req_sources=_source_report(engine,names,'REQUIREMENT'); prop_sources=_source_report(engine,names,'PROPERTY')
    return {'status':'READY','version':VERSION,'authority':{
      'requirement_gate_total':gate,'requirement_gate_active':gate_active,'requirement_matcher_eligible':gate_match,
      'canonical_requirement_rows':master_req,'canonical_property_rows':master_prop,
      'linked_requirement_canonical_ids':links_req,'linked_property_canonical_ids':links_prop,
      'property_availability':availability},
      'manual_requirement_sources':[x for x in req_sources if x.get('manual_source')],
      'requirement_sources':req_sources,'property_sources':prop_sources,
      'requirement_unlinked_estimate':sum(int(x.get('unlinked_estimate',0)) for x in req_sources),
      'property_unlinked_estimate':sum(int(x.get('unlinked_estimate',0)) for x in prop_sources),
      'warning':'Unlinked estimates are lineage gaps, not automatic promotion approval.',
      'database_changed':False}

def register(core):
    app=getattr(core,'app',None) or core; router=APIRouter()
    @router.get('/api/alliance/master-reconciliation-v1/audit')
    def route(req:Request): _role(core,req); return audit(core.engine)
    app.include_router(router)
    return {'status':'REGISTERED','version':VERSION,'automatic_promotion':False,'database_mutation':False}
