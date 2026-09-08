from __future__ import annotations

import hashlib
import json
import re
from sqlalchemy import text

VERSION='12.4.26-CANONICAL-OPERATIONAL-MASTER-BRIDGE'
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
    return d[-10:] if len(d)>=10 else (d or None)

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
        'master_entity_type':entity_type,
        'master_id':master_id,
        'canonical_id':cid,
        'source_type':'MANUAL_OPERATIONAL',
        'source_table':source_table,
        'source_pk':str(source_pk),
        'source_row_hash':row_hash,
    }
    keys=[k for k in data if k in cols]
    col_sql=','.join(keys)
    val_sql=','.join(':'+k for k in keys)
    sql='INSERT INTO pi_master_source_links_v711('+col_sql+') VALUES('+val_sql+') ON CONFLICT DO NOTHING'
    conn.execute(text(sql),{k:data[k] for k in keys})

def _workflow(conn,cid,entity_type,verified=False,available=None,actor='team'):
    status='VERIFIED' if verified else 'UNVERIFIED'
    avail=available if available is not None else ('ACTIVE' if entity_type=='REQUIREMENT' else 'UNKNOWN')
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
    """),{'cid':cid,'et':entity_type,'vs':status,'ok':verified,'actor':actor,'av':avail})

def sync_property(engine,property_code,actor='team'):
    with engine.begin() as c:
        row=c.execute(text('SELECT * FROM '+PROPERTY_TABLE+' WHERE property_code=:id FOR UPDATE'),{'id':property_code}).mappings().first()
        if not row:
            raise RuntimeError(f'Operational property not found: {property_code}')
        r=dict(row)
        cid='MANUAL-PROP-'+_hash('PROPERTY',property_code)[:24].upper()
        mid='MP-'+_hash(cid)[:16].upper()
        tx=_tx(r.get('transaction_type'))
        amount=r.get('rent_amount')
        phones=[_phone(r.get('contact_number'))] if _phone(r.get('contact_number')) else []
        clean={
            'manual_operational':{
                'property_code':property_code,'division':r.get('division'),'property_name':r.get('property_name'),
                'property_types':r.get('property_types'),'location':r.get('location'),'google_location':r.get('google_location'),
                'area_text':r.get('area_text'),'rent_text':r.get('rent_text'),'floor':r.get('floor'),
                'frontage':r.get('frontage'),'parking':r.get('parking'),'possession':r.get('possession'),
                'suitable_for':r.get('suitable_for'),'nearby_brands':r.get('nearby_brands'),
                'owner_broker_name':r.get('owner_broker_name'),'contact_number':r.get('contact_number'),
                'contact_role':r.get('contact_role'),'remarks':r.get('remarks'),'bridge_version':VERSION,
            },
            'property_name':r.get('property_name'),'property_type':r.get('property_types'),
            'description':r.get('remarks'),'floor':r.get('floor'),'suitable_category':r.get('suitable_for'),
        }
        data={
            'master_property_id':mid,'canonical_id':cid,'source_type':'MANUAL_OPERATIONAL','transaction_type':tx,
            'locality':r.get('location'),'city':r.get('city'),'area_value':r.get('area_sqft'),'area_unit':'SQFT',
            'area_sqft':r.get('area_sqft'),'price_raw':str(r.get('rent_text') or amount or ''),
            'price_kind':'SALE_AMOUNT' if tx=='SALE' else 'RENT_AMOUNT','phones':json.dumps(phones),
            'clean_record':json.dumps(clean,ensure_ascii=False,default=str),'source_count':1,
            'promotion_status':'PROMOTED_VALIDATED','source_version':VERSION,
        }
        _insert_dynamic(c,'pi_master_properties_v711',data)
        actual_mid=c.execute(text('SELECT master_property_id FROM pi_master_properties_v711 WHERE canonical_id=:cid'),{'cid':cid}).scalar() or mid
        rh=_hash(property_code,r.get('location'),r.get('area_sqft'),r.get('rent_amount'),r.get('contact_number'))
        _source_link(c,'PROPERTY',str(actual_mid),cid,PROPERTY_TABLE,property_code,rh)
        verified=_norm(r.get('verification_status')).upper()=='VERIFIED'
        _workflow(c,cid,'PROPERTY',verified,'AVAILABLE' if verified else 'UNKNOWN',actor)
    return {'status':'SYNCED','entity_type':'PROPERTY','property_code':property_code,'canonical_id':cid,'master_id':str(actual_mid),'verified':verified}

def sync_requirement(engine,requirement_code,actor='team'):
    with engine.begin() as c:
        row=c.execute(text('SELECT * FROM '+REQ_TABLE+' WHERE requirement_code=:id FOR UPDATE'),{'id':requirement_code}).mappings().first()
        if not row:
            raise RuntimeError(f'Operational requirement not found: {requirement_code}')
        r=dict(row)
        cid='MANUAL-REQ-'+_hash('REQUIREMENT',requirement_code)[:24].upper()
        mid='MR-'+_hash(cid)[:16].upper()
        tx=_tx(r.get('transaction_type'))
        mina=r.get('minimum_area_sqft'); maxa=r.get('maximum_area_sqft')
        area=None
        try:
            if mina is not None and maxa is not None: area=(float(mina)+float(maxa))/2
            elif mina is not None: area=float(mina)
            elif maxa is not None: area=float(maxa)
        except Exception:
            area=None
        budget=r.get('maximum_rent')
        phones=[_phone(r.get('contact_number'))] if _phone(r.get('contact_number')) else []
        clean={
            'manual_operational':{
                'requirement_code':requirement_code,'division':r.get('division'),'client_name':r.get('client_name'),
                'company_name':r.get('company_name'),'requirement_types':r.get('requirement_types'),
                'preferred_locations':r.get('preferred_locations'),'minimum_area_sqft':mina,'maximum_area_sqft':maxa,
                'minimum_area_text':r.get('minimum_area_text'),'maximum_area_text':r.get('maximum_area_text'),
                'maximum_rent_text':r.get('maximum_rent_text'),'additional_points':r.get('additional_points'),
                'bridge_version':VERSION,
            },
            'client_company':r.get('company_name'),'contact_name':r.get('client_name'),
            'contact_no':phones[0] if phones else None,'contact_numbers':phones,
            'property_type':r.get('requirement_types'),'intended_use':r.get('requirement_types'),
            'area_min_sqft':mina,'area_max_sqft':maxa,
            'requirement_text':r.get('additional_points') or r.get('preferred_locations'),
            'original_message':r.get('additional_points') or r.get('preferred_locations'),
        }
        verified=_norm(r.get('verification_status')).upper()=='VERIFIED'
        data={
            'master_requirement_id':mid,'canonical_id':cid,'source_type':'MANUAL_OPERATIONAL',
            'transaction_type':tx,'locality':r.get('preferred_locations'),'city':r.get('city'),
            'area_value':area,'area_unit':'SQFT','area_sqft':area,
            'budget_raw':str(r.get('maximum_rent_text') or budget or ''),
            'budget_kind':'SALE_AMOUNT' if tx=='SALE' else 'RENT_AMOUNT',
            'phones':json.dumps(phones),'clean_record':json.dumps(clean,ensure_ascii=False,default=str),
            'source_count':1,'promotion_status':'PROMOTED_VALIDATED' if verified else 'NEEDS_VERIFICATION',
            'source_version':VERSION,
        }
        _insert_dynamic(c,'pi_master_requirements_v711',data)
        actual_mid=c.execute(text('SELECT master_requirement_id FROM pi_master_requirements_v711 WHERE canonical_id=:cid'),{'cid':cid}).scalar() or mid
        rh=_hash(requirement_code,r.get('preferred_locations'),mina,maxa,budget,r.get('contact_number'))
        _source_link(c,'REQUIREMENT',str(actual_mid),cid,REQ_TABLE,requirement_code,rh)
        _workflow(c,cid,'REQUIREMENT',verified,'ACTIVE' if verified else 'INACTIVE',actor)
    return {'status':'SYNCED','entity_type':'REQUIREMENT','requirement_code':requirement_code,'canonical_id':cid,'master_id':str(actual_mid),'verified':verified,'matcher_visible':verified}

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
