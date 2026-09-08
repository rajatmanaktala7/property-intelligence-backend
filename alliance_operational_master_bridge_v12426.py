from __future__ import annotations

import hashlib
import json
import re
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

VERSION='12.4.26A-CANONICAL-LIFECYCLE-HARDENING'
PROPERTY_TABLE='pi_operational_properties'
REQ_TABLE='pi_operational_requirements'

def _norm(v):
    return ' '.join(str(v or '').split()).strip()

def _tx(v):
    x=_norm(v).upper()
    if x in {'LEASE','RENT'}: return 'RENT'
    if x in {'SALE','PURCHASE','BUY'}: return 'SALE'
    return x or None

def _phone(v):
    d=re.sub(r'\D','',str(v or ''))
    if len(d)>10: d=d[-10:]
    return d if len(d)==10 else None

def _hash(*parts):
    return hashlib.sha256('|'.join(_norm(x) for x in parts).encode('utf-8')).hexdigest()

def _columns(conn, table):
    return {r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:t"
    ),{'t':table}).all()}

def _required(conn, table):
    return {r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:t AND is_nullable='NO' AND column_default IS NULL"
    ),{'t':table}).all()}

def _insert_dynamic(conn, table, data, conflict_col='canonical_id'):
    cols=_columns(conn,table)
    req=_required(conn,table)
    payload={k:v for k,v in data.items() if k in cols}
    missing=[k for k in req if k not in payload]
    if missing:
        raise RuntimeError(f'{table} required columns not mapped: {missing}')
    keys=list(payload)
    if not keys:
        raise RuntimeError(f'No compatible columns found for {table}')
    col_sql=','.join('"'+k+'"' for k in keys)
    val_sql=','.join(':'+k for k in keys)
    updates=[k for k in keys if k not in {conflict_col,'created_at','master_property_id','master_requirement_id'}]
    sql=f'INSERT INTO "{table}" ({col_sql}) VALUES ({val_sql})'
    if conflict_col in cols:
        if updates:
            update_sql=','.join('"'+k+'"=EXCLUDED."'+k+'"' for k in updates)
            sql+=f' ON CONFLICT("{conflict_col}") DO UPDATE SET {update_sql}'
        else:
            sql+=f' ON CONFLICT("{conflict_col}") DO NOTHING'
    conn.execute(text(sql),payload)

def _source_link(conn,entity_type,master_id,cid,source_table,source_pk,row_hash):
    cols=_columns(conn,'pi_master_source_links_v711')
    if not cols:
        return
    data={
        'master_entity_type':entity_type,'master_id':master_id,'canonical_id':cid,
        'source_type':'MANUAL_OPERATIONAL','source_table':source_table,
        'source_pk':str(source_pk),'source_row_hash':row_hash,
    }
    keys=[k for k in data if k in cols]
    sql='INSERT INTO pi_master_source_links_v711('+','.join(keys)+') VALUES('+','.join(':'+k for k in keys)+') ON CONFLICT DO NOTHING'
    conn.execute(text(sql),{k:data[k] for k in keys})

def _workflow(conn,cid,entity_type,verified=False,availability='UNKNOWN',actor='team'):
    conn.execute(text("""
      INSERT INTO pi_master_workflow_v720(
        canonical_id,entity_type,verification_status,verified_at,verified_by,
        availability_status,updated_at
      ) VALUES(:cid,:et,:vs,CASE WHEN :ok THEN NOW() ELSE NULL END,
               CASE WHEN :ok THEN :actor ELSE NULL END,:av,NOW())
      ON CONFLICT(canonical_id) DO UPDATE SET
        entity_type=EXCLUDED.entity_type,
        verification_status=EXCLUDED.verification_status,
        verified_at=EXCLUDED.verified_at,
        verified_by=EXCLUDED.verified_by,
        availability_status=EXCLUDED.availability_status,
        updated_at=NOW()
    """),{
        'cid':cid,'et':entity_type,'vs':'VERIFIED' if verified else 'UNVERIFIED',
        'ok':verified,'actor':actor,'av':availability
    })

def _requirement_gate_ok(r):
    tx=_tx(r.get('transaction_type'))
    loc=_norm(r.get('preferred_locations'))
    ph=_phone(r.get('contact_number'))
    verified=_norm(r.get('verification_status')).upper()=='VERIFIED'
    reasons=[]
    if not verified: reasons.append('verification_status is not VERIFIED')
    if tx not in {'RENT','SALE'}: reasons.append('transaction must be LEASE/RENT/SALE')
    if not loc: reasons.append('preferred location is required')
    if not ph: reasons.append('valid 10 digit contact number is required')
    return (not reasons),reasons

def _property_cid(code): return 'MANUAL-PROP-'+_hash('PROPERTY',code)[:24].upper()
def _requirement_cid(code): return 'MANUAL-REQ-'+_hash('REQUIREMENT',code)[:24].upper()

def sync_property(engine,property_code,actor='team'):
    with engine.begin() as c:
        row=c.execute(text('SELECT * FROM '+PROPERTY_TABLE+' WHERE property_code=:id FOR UPDATE'),{'id':property_code}).mappings().first()
        if not row: raise RuntimeError(f'Operational property not found: {property_code}')
        r=dict(row)
        cid=_property_cid(property_code); mid='MP-'+_hash(cid)[:16].upper()
        tx=_tx(r.get('transaction_type'))
        phones=[_phone(r.get('contact_number'))] if _phone(r.get('contact_number')) else []
        clean={
            'manual_operational':{
                'property_code':property_code,'division':r.get('division'),'property_name':r.get('property_name'),
                'property_types':r.get('property_types'),'location':r.get('location'),'google_location':r.get('google_location'),
                'area_text':r.get('area_text'),'rent_text':r.get('rent_text'),'floor':r.get('floor'),
                'frontage':r.get('frontage'),'parking':r.get('parking'),'possession':r.get('possession'),
                'suitable_for':r.get('suitable_for'),'nearby_brands':r.get('nearby_brands'),
                'owner_broker_name':r.get('owner_broker_name'),'contact_number':r.get('contact_number'),
                'contact_role':r.get('contact_role'),'verification_status':r.get('verification_status'),
                'remarks':r.get('remarks'),'bridge_version':VERSION,
            },
            'property_name':r.get('property_name'),'property_type':r.get('property_types'),
            'description':r.get('remarks'),'floor':r.get('floor'),'suitable_category':r.get('suitable_for'),
        }
        data={
            'master_property_id':mid,'canonical_id':cid,'source_type':'MANUAL_OPERATIONAL',
            'transaction_type':tx,'locality':r.get('location'),'city':r.get('city'),
            'area_value':r.get('area_sqft'),'area_unit':'SQFT','area_sqft':r.get('area_sqft'),
            'price_raw':str(r.get('rent_text') or r.get('rent_amount') or ''),
            'price_kind':'SALE_AMOUNT' if tx=='SALE' else 'RENT_AMOUNT',
            'phones':json.dumps(phones),'clean_record':json.dumps(clean,ensure_ascii=False,default=str),
            'source_count':1,'promotion_status':'PROMOTED_VALIDATED','source_version':VERSION,
        }
        _insert_dynamic(c,'pi_master_properties_v711',data)
        actual_mid=c.execute(text('SELECT master_property_id FROM pi_master_properties_v711 WHERE canonical_id=:cid'),{'cid':cid}).scalar() or mid
        _source_link(c,'PROPERTY',str(actual_mid),cid,PROPERTY_TABLE,property_code,
                     _hash(property_code,r.get('location'),r.get('area_sqft'),r.get('rent_amount'),r.get('contact_number')))
        verified=_norm(r.get('verification_status')).upper()=='VERIFIED'
        # Identity/details verification is NOT the same as current availability.
        _workflow(c,cid,'PROPERTY',verified,'UNKNOWN',actor)
    return {'status':'SYNCED','entity_type':'PROPERTY','property_code':property_code,
            'canonical_id':cid,'master_id':str(actual_mid),'verified':verified,'availability':'UNKNOWN'}

def sync_requirement(engine,requirement_code,actor='team'):
    with engine.begin() as c:
        row=c.execute(text('SELECT * FROM '+REQ_TABLE+' WHERE requirement_code=:id FOR UPDATE'),{'id':requirement_code}).mappings().first()
        if not row: raise RuntimeError(f'Operational requirement not found: {requirement_code}')
        r=dict(row)
        cid=_requirement_cid(requirement_code); mid='MR-'+_hash(cid)[:16].upper()
        tx=_tx(r.get('transaction_type')); mina=r.get('minimum_area_sqft'); maxa=r.get('maximum_area_sqft')
        try:
            area=(float(mina)+float(maxa))/2 if mina is not None and maxa is not None else float(mina if mina is not None else maxa)
        except Exception:
            area=None
        ph=_phone(r.get('contact_number')); phones=[ph] if ph else []
        gate_ok,reasons=_requirement_gate_ok(r)
        clean={
            'manual_operational':{
                'requirement_code':requirement_code,'division':r.get('division'),'client_name':r.get('client_name'),
                'company_name':r.get('company_name'),'requirement_types':r.get('requirement_types'),
                'preferred_locations':r.get('preferred_locations'),'minimum_area_sqft':mina,'maximum_area_sqft':maxa,
                'minimum_area_text':r.get('minimum_area_text'),'maximum_area_text':r.get('maximum_area_text'),
                'maximum_rent_text':r.get('maximum_rent_text'),'additional_points':r.get('additional_points'),
                'verification_status':r.get('verification_status'),'gate_reasons':reasons,'bridge_version':VERSION,
            },
            'client_company':r.get('company_name'),'contact_name':r.get('client_name'),
            'contact_no':ph,'contact_numbers':phones,'property_type':r.get('requirement_types'),
            'intended_use':r.get('requirement_types'),'area_min_sqft':mina,'area_max_sqft':maxa,
            'requirement_text':r.get('additional_points') or r.get('preferred_locations'),
            'original_message':r.get('additional_points') or r.get('preferred_locations'),
        }
        data={
            'master_requirement_id':mid,'canonical_id':cid,'source_type':'MANUAL_OPERATIONAL',
            'transaction_type':tx,'locality':r.get('preferred_locations'),'city':r.get('city'),
            'area_value':area,'area_unit':'SQFT','area_sqft':area,
            'budget_raw':str(r.get('maximum_rent_text') or r.get('maximum_rent') or ''),
            'budget_kind':'SALE_AMOUNT' if tx=='SALE' else 'RENT_AMOUNT',
            'phones':json.dumps(phones),'clean_record':json.dumps(clean,ensure_ascii=False,default=str),
            'source_count':1,'promotion_status':'PROMOTED_VALIDATED' if gate_ok else 'NEEDS_VERIFICATION',
            'source_version':VERSION,
        }
        _insert_dynamic(c,'pi_master_requirements_v711',data)
        actual_mid=c.execute(text('SELECT master_requirement_id FROM pi_master_requirements_v711 WHERE canonical_id=:cid'),{'cid':cid}).scalar() or mid
        _source_link(c,'REQUIREMENT',str(actual_mid),cid,REQ_TABLE,requirement_code,
                     _hash(requirement_code,r.get('preferred_locations'),mina,maxa,r.get('maximum_rent'),r.get('contact_number')))
        _workflow(c,cid,'REQUIREMENT',gate_ok,'ACTIVE' if gate_ok else 'INACTIVE',actor)
        if not gate_ok:
            c.execute(text("DELETE FROM pi_master_matches_v720 WHERE requirement_canonical_id=:cid"),{'cid':cid})
    return {'status':'SYNCED','entity_type':'REQUIREMENT','requirement_code':requirement_code,
            'canonical_id':cid,'master_id':str(actual_mid),'verified':gate_ok,
            'matcher_visible':gate_ok,'gate_reasons':reasons}

def withdraw_property(engine,property_code,actor='team'):
    cid=_property_cid(property_code)
    with engine.begin() as c:
        c.execute(text("""
          UPDATE pi_master_properties_v711 SET promotion_status='MANUAL_ARCHIVED',
          source_version=:v,updated_at=NOW() WHERE canonical_id=:cid
        """),{'cid':cid,'v':VERSION})
        _workflow(c,cid,'PROPERTY',False,'INACTIVE',actor)
        c.execute(text("DELETE FROM pi_master_matches_v720 WHERE property_canonical_id=:cid"),{'cid':cid})
    return {'status':'ARCHIVED','entity_type':'PROPERTY','property_code':property_code,'canonical_id':cid}

def withdraw_requirement(engine,requirement_code,actor='team'):
    cid=_requirement_cid(requirement_code)
    with engine.begin() as c:
        c.execute(text("""
          UPDATE pi_master_requirements_v711 SET promotion_status='MANUAL_ARCHIVED',
          source_version=:v,updated_at=NOW() WHERE canonical_id=:cid
        """),{'cid':cid,'v':VERSION})
        _workflow(c,cid,'REQUIREMENT',False,'INACTIVE',actor)
        c.execute(text("DELETE FROM pi_master_matches_v720 WHERE requirement_canonical_id=:cid"),{'cid':cid})
    return {'status':'ARCHIVED','entity_type':'REQUIREMENT','requirement_code':requirement_code,'canonical_id':cid}

def diagnostic(engine,entity_type,operational_id):
    et=_norm(entity_type).upper()
    if et=='PROPERTY':
        cid=_property_cid(operational_id); master_table='pi_master_properties_v711'; mid_col='master_property_id'
    elif et=='REQUIREMENT':
        cid=_requirement_cid(operational_id); master_table='pi_master_requirements_v711'; mid_col='master_requirement_id'
    else:
        raise ValueError('entity_type must be PROPERTY or REQUIREMENT')
    with engine.connect() as c:
        m=c.execute(text(f"SELECT {mid_col} master_id,canonical_id,promotion_status,source_type,source_version FROM {master_table} WHERE canonical_id=:cid"),{'cid':cid}).mappings().first()
        w=c.execute(text("SELECT canonical_id,entity_type,verification_status,availability_status,verified_by,verified_at,updated_at FROM pi_master_workflow_v720 WHERE canonical_id=:cid"),{'cid':cid}).mappings().first()
        links=[dict(x) for x in c.execute(text("""
          SELECT master_entity_type,master_id,canonical_id,source_type,source_table,source_pk
          FROM pi_master_source_links_v711 WHERE canonical_id=:cid ORDER BY id
        """),{'cid':cid}).mappings().all()]
    return {'version':VERSION,'entity_type':et,'operational_id':operational_id,'canonical_id':cid,
            'master':dict(m) if m else None,'workflow':dict(w) if w else None,'source_links':links}

def audit(engine):
    with engine.connect() as c:
        p=c.execute(text("""
          SELECT COUNT(*) FROM pi_operational_properties o
          WHERE COALESCE(o.entry_source,'MANUAL')='MANUAL'
            AND NOT EXISTS(SELECT 1 FROM pi_master_source_links_v711 l
              WHERE l.master_entity_type='PROPERTY' AND l.source_table=:tb AND l.source_pk=o.property_code)
        """),{'tb':PROPERTY_TABLE}).scalar()
        r=c.execute(text("""
          SELECT COUNT(*) FROM pi_operational_requirements o
          WHERE COALESCE(o.entry_source,'MANUAL')='MANUAL'
            AND NOT EXISTS(SELECT 1 FROM pi_master_source_links_v711 l
              WHERE l.master_entity_type='REQUIREMENT' AND l.source_table=:tb AND l.source_pk=o.requirement_code)
        """),{'tb':REQ_TABLE}).scalar()
    return {'version':VERSION,'unbridged_manual_properties':int(p or 0),'unbridged_manual_requirements':int(r or 0)}

def register(core):
    app=getattr(core,'app',None) or core
    engine=core.engine
    app.router.routes[:]=[r for r in list(app.router.routes) if getattr(r,'path',None) not in {
        '/api/alliance/canonical-bridge/audit',
        '/api/alliance/canonical-bridge/diagnostic/{entity_type}/{operational_id}'
    }]
    @app.get('/api/alliance/canonical-bridge/audit')
    def bridge_audit(req:Request):
        if hasattr(core,'need_login'): core.need_login(req)
        return JSONResponse(audit(engine))
    @app.get('/api/alliance/canonical-bridge/diagnostic/{entity_type}/{operational_id}')
    def bridge_diagnostic(entity_type:str,operational_id:str,req:Request):
        if hasattr(core,'need_login'): core.need_login(req)
        try:
            return JSONResponse(diagnostic(engine,entity_type,operational_id),default=str)
        except TypeError:
            return diagnostic(engine,entity_type,operational_id)
    return {'status':'REGISTERED','version':VERSION,'audit_route':'/api/alliance/canonical-bridge/audit'}
