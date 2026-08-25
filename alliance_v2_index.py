import re,json
from sqlalchemy import text
from alliance_v2_schema import VERSION,exists
from alliance_v2_normalize import *

PS=[
("pi_operational_properties","MANUAL_SURVEY","Alliance Manual / Surveyor",{
"id":["property_code"],"name":["property_name"],"location":["location"],"city":["city"],
"type":["property_types"],"transaction":["transaction_type"],"area":["area_text"],
"area_num":["area_sqft"],"area_max":["area_sqft"],"rent":["rent_text"],"rent_num":["rent_amount"],
"phone":["contact_number"],"person":["owner_broker_name"],"verification":["verification_status"],
"availability":["availability_status"],"floor":["floor"],"frontage":["frontage"],
"suitable":["suitable_for"],"nearby":["nearby_brands"]
}),
("pi_properties","LEGACY","Alliance Master Property Database",{
"id":["property_id"],"name":["property_name"],"location":["location"],"city":["city"],
"type":["property_type"],"transaction":["rent_or_sale"],
"area_num":["available_area_sqft","minimum_area_sqft"],"area_max":["maximum_area_sqft","available_area_sqft"],
"rent":["remarks"],"phone":["owner_contact","broker_contact"],"person":["owner_name","broker_name"],
"verification":["verification_status"],"availability":["availability_status"],
"floor":["floor"],"frontage":["frontage"],"suitable":["suitable_category"],"nearby":["nearby_brands"]
}),
("pi_newspaper_properties","NEWSPAPER","Newspaper Property Database",{
"id":["record_id"],"name":["configuration_details"],"location":["locality"],
"type":["configuration_details"],"transaction":["lead_type"],"area":["area"],"rent":["price"],
"phone":["phone_numbers"],"person":["contact_person"],"company":["agency_brand"],
"verification":["verification"],"floor":["configuration_details"],"frontage":["configuration_details"],
"suitable":["configuration_details"]
})
]

RS=[
("pi_operational_requirements","MANUAL","Alliance Manual Requirements",{
"id":["requirement_code"],"area_min":["minimum_area_sqft","minimum_area_text"],
"area_max":["maximum_area_sqft","maximum_area_text"],"rent":["maximum_rent","maximum_rent_text"],
"locations":["preferred_locations"],"transaction":["transaction_type"],"types":["requirement_types"],
"verification":["verification_status"],"status":["status"],"additional":["additional_points"]
}),
("pi_requirements","LEGACY","Alliance Master Requirements",{
"id":["requirement_id"],"area_min":["minimum_area_sqft"],"area_max":["maximum_area_sqft"],
"locations":["preferred_locations"],"transaction":["rent_or_sale"],
"types":["property_type","requirement_type"],"status":["status"],"additional":["additional_points"]
})
]

def index_prop(c,r,t,st,sn,m):
    rid=str(pick(r,*m["id"]) or "")
    if not rid:return 0
    loc=pick(r,*m.get("location",[]))
    city=pick(r,*m.get("city",[]))
    typ=ptype(pick(r,*m.get("type",[])))
    tran=tx(pick(r,*m.get("transaction",[])) or pick(r,"lead_type"))
    amin,amax=area(pick(r,*m.get("area",[])))
    if amin is None:
        amin=num(pick(r,*m.get("area_num",[])))
        amax=num(pick(r,*m.get("area_max",[]))) or amin
    rawrent=pick(r,*m.get("rent",[]))
    mv=money(rawrent) or num(pick(r,*m.get("rent_num",[])))
    ver=str(pick(r,*m.get("verification",[])) or "UNVERIFIED").upper()
    avail=str(pick(r,*m.get("availability",[])) or "UNKNOWN").upper()
    cf=CONF.get(st,50)
    ct=contact(c,pick(r,*m.get("phone",[])),pick(r,*m.get("person",[])),pick(r,*m.get("company",[])),cf)
    floor_raw=pick(r,*m.get("floor",[]))
    frontage_raw=pick(r,*m.get("frontage",[]))
    suitable=pick(r,*m.get("suitable",[]))
    nearby=pick(r,*m.get("nearby",[]))
    comp=(20 if loc else 0)+(20 if amin else 0)+(15 if tran!="UNKNOWN" else 0)+(15 if typ!="UNKNOWN" else 0)+(10 if ct else 0)+(10 if mv else 0)+(5 if floor_raw else 0)+(5 if frontage_raw else 0)
    eligible=bool(loc and amin and tran!="UNKNOWN" and typ!="UNKNOWN" and avail not in {"NOT AVAILABLE","SOLD","LEASED","INACTIVE"})
    i=pid(t,rid)
    name=pick(r,*m.get("name",[])) or f"{loc or 'Unknown'} {typ}"
    payload=json.dumps(r,default=str)
    rpsf=monthly=sale=None
    if tran=="SALE":sale=mv
    elif mv:
        if any(x in norm(rawrent) for x in ["psf","sq ft","sqft","per sq"]):
            rpsf=mv;monthly=round(mv*amin,2) if amin else None
        else:
            monthly=mv;rpsf=round(mv/amin,2) if amin else None
    c.execute(text("""INSERT INTO ai_property_identity(property_identity_id,canonical_label,last_seen_at)
      VALUES(:i,:n,NOW())
      ON CONFLICT(property_identity_id) DO UPDATE SET
      canonical_label=COALESCE(EXCLUDED.canonical_label,ai_property_identity.canonical_label),last_seen_at=NOW()"""),{"i":i,"n":name})
    c.execute(text("""INSERT INTO ai_property_match_index(
      property_identity_id,source_type,source_name,source_table,source_record_id,property_name,
      canonical_property_type,city,location_raw,location_normalized,transaction_type,
      area_min_sqft,area_max_sqft,rent_original,rent_psf_month,monthly_rent,sale_price,
      floor_raw,floor_normalized,frontage_raw,frontage_ft,suitable_for,nearby_brands,
      verification_status,availability_status,contact_reference_id,data_completeness_score,
      data_confidence_score,source_confidence_score,match_eligible,original_payload,
      normalization_version,updated_at)
      VALUES(:i,:st,:sn,:t,:rid,:n,:typ,:city,:loc,:ln,:tr,:amin,:amax,:rr,:rp,:mr,:sp,
      :fr,:fn,:frr,:fft,:suit,:near,:ver,:av,:ct,:comp,:comp,:cf,:el,CAST(:pl AS jsonb),:v,NOW())
      ON CONFLICT(source_table,source_record_id) DO UPDATE SET
      property_name=EXCLUDED.property_name,canonical_property_type=EXCLUDED.canonical_property_type,
      city=EXCLUDED.city,location_raw=EXCLUDED.location_raw,location_normalized=EXCLUDED.location_normalized,
      transaction_type=EXCLUDED.transaction_type,area_min_sqft=EXCLUDED.area_min_sqft,
      area_max_sqft=EXCLUDED.area_max_sqft,rent_original=EXCLUDED.rent_original,
      rent_psf_month=EXCLUDED.rent_psf_month,monthly_rent=EXCLUDED.monthly_rent,sale_price=EXCLUDED.sale_price,
      floor_raw=EXCLUDED.floor_raw,floor_normalized=EXCLUDED.floor_normalized,
      frontage_raw=EXCLUDED.frontage_raw,frontage_ft=EXCLUDED.frontage_ft,
      suitable_for=EXCLUDED.suitable_for,nearby_brands=EXCLUDED.nearby_brands,
      verification_status=EXCLUDED.verification_status,availability_status=EXCLUDED.availability_status,
      contact_reference_id=EXCLUDED.contact_reference_id,data_completeness_score=EXCLUDED.data_completeness_score,
      data_confidence_score=EXCLUDED.data_confidence_score,source_confidence_score=EXCLUDED.source_confidence_score,
      match_eligible=EXCLUDED.match_eligible,original_payload=EXCLUDED.original_payload,
      normalization_version=EXCLUDED.normalization_version,updated_at=NOW()"""),
      {"i":i,"st":st,"sn":sn,"t":t,"rid":rid,"n":name,"typ":typ,"city":city,"loc":loc,"ln":norm(loc),
       "tr":tran,"amin":amin,"amax":amax,"rr":str(rawrent or ""),"rp":rpsf,"mr":monthly,"sp":sale,
       "fr":str(floor_raw or ""),"fn":floor_norm(floor_raw),"frr":str(frontage_raw or ""),
       "fft":frontage_ft(frontage_raw) or infer_frontage(frontage_raw),"suit":str(suitable or ""),
       "near":str(nearby or ""),"ver":ver,"av":avail,"ct":ct,"comp":comp,"cf":cf,"el":eligible,
       "pl":payload,"v":VERSION})
    return 1

def index_req(c,r,t,st,sn,m):
    rid=str(pick(r,*m["id"]) or "")
    if not rid:return 0
    amin=num(pick(r,*m.get("area_min",[]))) or area(pick(r,*m.get("area_min",[])))[0]
    amax=num(pick(r,*m.get("area_max",[]))) or area(pick(r,*m.get("area_max",[])))[1]
    rent=num(pick(r,*m.get("rent",[]))) or money(pick(r,*m.get("rent",[])))
    locs=pick(r,*m.get("locations",[]))
    tran=tx(pick(r,*m.get("transaction",[])))
    types=req_types(pick(r,*m.get("types",[])))
    ver=str(pick(r,*m.get("verification",[])) or "UNVERIFIED").upper()
    status=str(pick(r,*m.get("status",[])) or "ACTIVE").upper()
    additional=str(pick(r,*m.get("additional",[])) or "")
    min_frontage=infer_frontage(additional)
    req_floor=infer_required_floor(additional)
    suitable=infer_suitable(additional)
    eligible=bool(locs and amin and amax and tran!="UNKNOWN" and types and status not in {"CLOSED","LOST","INACTIVE"})
    payload=json.dumps(r,default=str)
    q=c.execute(text("""INSERT INTO ai_requirement_index(
      source_table,source_record_id,requirement_code,source_type,source_name,client_name,company_name,
      transaction_type,requirement_types,preferred_locations_raw,minimum_area_sqft,maximum_area_sqft,
      maximum_monthly_rent,minimum_frontage_ft,required_floor,suitable_for,additional_points,
      verification_status,status,match_eligible,original_payload,normalization_version,updated_at)
      VALUES(:t,:rid,:rid,:st,:sn,:cl,:co,:tr,CAST(:ty AS jsonb),:loc,:amin,:amax,:rent,
      :front,:floor,:suit,:add,:ver,:status,:el,CAST(:pl AS jsonb),:v,NOW())
      ON CONFLICT(source_table,source_record_id) DO UPDATE SET
      client_name=EXCLUDED.client_name,company_name=EXCLUDED.company_name,
      transaction_type=EXCLUDED.transaction_type,requirement_types=EXCLUDED.requirement_types,
      preferred_locations_raw=EXCLUDED.preferred_locations_raw,minimum_area_sqft=EXCLUDED.minimum_area_sqft,
      maximum_area_sqft=EXCLUDED.maximum_area_sqft,maximum_monthly_rent=EXCLUDED.maximum_monthly_rent,
      minimum_frontage_ft=EXCLUDED.minimum_frontage_ft,required_floor=EXCLUDED.required_floor,
      suitable_for=EXCLUDED.suitable_for,additional_points=EXCLUDED.additional_points,
      verification_status=EXCLUDED.verification_status,status=EXCLUDED.status,
      match_eligible=EXCLUDED.match_eligible,original_payload=EXCLUDED.original_payload,
      normalization_version=EXCLUDED.normalization_version,updated_at=NOW()
      RETURNING requirement_index_id"""),
      {"t":t,"rid":rid,"st":st,"sn":sn,"cl":pick(r,"client_name"),"co":pick(r,"company_name"),
       "tr":tran,"ty":json.dumps(types),"loc":locs,"amin":amin,"amax":amax,"rent":rent,
       "front":min_frontage,"floor":req_floor,"suit":suitable,"add":additional,
       "ver":ver,"status":status,"el":eligible,"pl":payload,"v":VERSION}).scalar()
    for l in re.split(r"[,;/|]+|\bor\b",str(locs or ""),flags=re.I):
        if len(l.strip())>1:
            c.execute(text("""INSERT INTO ai_requirement_location(requirement_index_id,location_raw,location_normalized)
            VALUES(:q,:r,:n)
            ON CONFLICT(requirement_index_id,location_normalized) DO UPDATE SET location_raw=EXCLUDED.location_raw"""),
            {"q":q,"r":l.strip(),"n":norm(l)})
    return 1

def rebuild(engine):
    out={"properties":0,"requirements":0,"sources":[]}
    with engine.begin() as c:
        for t,st,sn,m in PS:
            if not exists(c,t):continue
            n=sum(index_prop(c,dict(x._mapping),t,st,sn,m) for x in c.execute(text(f'SELECT * FROM "{t}"')).fetchall())
            out["properties"]+=n;out["sources"].append({"table":t,"indexed":n})
        for t,st,sn,m in RS:
            if not exists(c,t):continue
            n=sum(index_req(c,dict(x._mapping),t,st,sn,m) for x in c.execute(text(f'SELECT * FROM "{t}"')).fetchall())
            out["requirements"]+=n;out["sources"].append({"table":t,"indexed":n})
    return out
