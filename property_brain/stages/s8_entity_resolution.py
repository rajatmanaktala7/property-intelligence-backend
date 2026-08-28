
import json
from uuid import uuid4
from sqlalchemy import text
from ..utils import fingerprint,money_label
def desc(f,loc):
    bits=[]
    if loc.locality_name:bits.append(loc.locality_name)
    if f.get("property_family"):bits.append(f["property_family"].title())
    if f.get("configuration"):bits.append(f["configuration"])
    if f.get("area"):bits.append(f'{f["area"]["value"]:g} {f["area"]["unit"]}')
    if f.get("money"):bits.append(("Rent " if f.get("transaction")=="RENT" else "Price ")+money_label(f["money"]["value"]))
    return " · ".join(bits)
def resolve_and_upsert(engine,x,loc,source_meta):
    f=x.fields;a=f.get("area");m=f.get("money");fp=fingerprint(loc.locality_name,f.get("property_family"),f.get("configuration"),round(a["sqft"]/50)*50 if a else "",f.get("transaction"),round(m["value"]/5000)*5000 if m else "")
    with engine.begin() as c:
        existing=c.execute(text("SELECT property_id FROM pb_canonical_properties WHERE fingerprint=:f AND current_status='ACTIVE' ORDER BY updated_at DESC LIMIT 1"),{"f":fp}).scalar();pid=existing or uuid4()
        if not existing:c.execute(text("""INSERT INTO pb_canonical_properties(property_id,fingerprint,transaction_type,property_family,locality,configuration,area_value,area_unit,area_sqft,rent_value,sale_price_value,contact_name,contact_numbers,clean_description,overall_confidence)
        VALUES(:id,:fp,:tx,:fam,:loc,:cfg,:av,:au,:asq,:rent,:sale,:cn,CAST(:nums AS jsonb),:d,:conf)"""),{"id":pid,"fp":fp,"tx":f.get("transaction"),"fam":f.get("property_family"),"loc":loc.locality_name,"cfg":f.get("configuration"),"av":a["value"] if a else None,"au":a["unit"] if a else None,"asq":a["sqft"] if a else None,"rent":m["value"] if m and f.get("transaction")=="RENT" else None,"sale":m["value"] if m and f.get("transaction")=="SALE" else None,"cn":source_meta.get("contact_name"),"nums":json.dumps(f.get("contact_numbers") or []),"d":desc(f,loc),"conf":float(x.field_confidence.get("overall",0))})
        for rid in x.raw_ids:c.execute(text("""INSERT INTO pb_property_sources(property_id,raw_id,source_type,source_ref,captured_at,contact_name,contact_numbers)
        SELECT :pid,raw_id,source_type,source_ref,captured_at,:cn,CAST(:nums AS jsonb) FROM pb_raw_evidence WHERE raw_id=:rid ON CONFLICT(property_id,raw_id) DO NOTHING"""),{"pid":pid,"rid":rid,"cn":source_meta.get("contact_name"),"nums":json.dumps(f.get("contact_numbers") or [])})
    return pid
