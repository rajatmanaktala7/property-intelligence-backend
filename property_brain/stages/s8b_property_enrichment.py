from __future__ import annotations
import json,re
from sqlalchemy import text

CRITICAL_FIELDS=["project_name","locality","property_family","property_subtype","transaction_type","area_sqft","floor","rent_value","sale_price_value"]

def _known(v): return v is not None and str(v).strip() and str(v).strip().upper() not in {"UNKNOWN","N/A","NA","NONE","NULL","—"}
def _phones(v):
    vals=v if isinstance(v,list) else [v];out=[]
    for x in vals:
        for p in re.findall(r"(?:\+?91[\s-]?)?[6-9]\d{9}",str(x or "")):
            n=re.sub(r"\D","",p)[-10:]
            if n not in out: out.append(n)
    return out

def build_description(p,missing):
    def v(k,d="Unknown"): return str(p.get(k)) if _known(p.get(k)) else d
    terms=[]
    if _known(p.get("rent_value")): terms.append(f"Rent: ₹{float(p['rent_value']):,.0f}")
    if _known(p.get("sale_price_value")): terms.append(f"Sale Price: ₹{float(p['sale_price_value']):,.0f}")
    return "\n".join([
      f"{v('property_subtype',v('property_family','Property'))} for {v('transaction_type','transaction')} in {v('locality')}",
      f"Project/Building: {v('project_name')}",
      f"Area: {v('area_sqft')} sq ft | Floor: {v('floor')} | Furnishing: {v('furnishing')}",
      " | ".join(terms) if terms else "Commercial Terms: Not stated",
      f"Contact: {v('contact_name')} | Phones: {', '.join(_phones(p.get('contact_numbers'))) or 'Unknown'}",
      f"Verification: {v('verification_status','UNVERIFIED')}",
      "Missing Critical Information: "+(", ".join(x.replace('_',' ').title() for x in missing) if missing else "None")])

def enrich_property(engine,property_id):
    with engine.connect() as c:
        row=c.execute(text("SELECT * FROM pb_canonical_properties WHERE property_id=:p"),{"p":property_id}).mappings().first()
    if not row:return {"status":"NOT_FOUND","property_id":str(property_id)}
    p=dict(row);changed={};provenance={k:{"status":"SOURCE-STATED"} for k,v in p.items() if _known(v)}
    with engine.connect() as c:
        others=c.execute(text("""SELECT * FROM pb_canonical_properties WHERE property_id<>:pid AND ((:pr<>'' AND UPPER(COALESCE(project_name,''))=UPPER(:pr)) OR (:loc<>'' AND :ct<>'' AND UPPER(COALESCE(locality,''))=UPPER(:loc) AND UPPER(COALESCE(contact_name,''))=UPPER(:ct))) ORDER BY (verification_status='VERIFIED') DESC,overall_confidence DESC LIMIT 25"""),{"pid":property_id,"pr":str(p.get('project_name') or ''),"loc":str(p.get('locality') or ''),"ct":str(p.get('contact_name') or '')}).mappings().all()
    fields=["project_name","locality","property_family","property_subtype","transaction_type","area_sqft","floor","furnishing","rent_value","sale_price_value"]
    for o in others:
        for f in fields:
            if not _known(p.get(f)) and _known(o.get(f)):
                p[f]=o[f];changed[f]=o[f];provenance[f]={"status":"CROSS-CONFIRMED","source_property_id":str(o['property_id'])}
    missing=[f for f in CRITICAL_FIELDS if not _known(p.get(f))]
    desc=build_description(p,missing)
    with engine.begin() as c:
        c.execute(text("UPDATE pb_canonical_properties SET clean_description=:d,updated_at=NOW() WHERE property_id=:p"),{"d":desc,"p":property_id})
    return {"status":"ENRICHED","property_id":str(property_id),"changed":changed,"missing_critical":missing,"provenance":provenance,"description":desc,"public_enrichment":"NOT_RUN"}
