
from sqlalchemy import text
from .s10_gates import eligibility
from ..utils import load_json
ALTS=load_json("alternative_locations.json")
def _score(req,p,kind):
    score=30 if kind=="EXACT" else 18;why=["Exact location" if kind=="EXACT" else "Alternative location"];missing=[]
    if req.property_family and p.get("property_family")==req.property_family:score+=18;why.append("Property type fits")
    area=p.get("area_sqft")
    if req.area_min_sqft and area is not None:
        score+=15 if req.area_min_sqft<=area<=req.area_max_sqft else 8
    else:missing.append("Area")
    price=p.get("rent_value") if req.transaction=="RENT" else p.get("sale_price_value")
    if req.budget_max and price is not None:
        score+=20 if price<=req.budget_max else (10 if price<=req.budget_max*1.1 else 0)
    else:missing.append("Price/Rent")
    score+=round(float(p.get("overall_confidence") or 0)*10,1)
    if p.get("verification_status")=="VERIFIED":score+=7;why.append("Verified")
    return min(100,round(score,1)),why,missing
def match(engine,req,min_score=60,limit=50):
    with engine.connect() as c:rows=[dict(r) for r in c.execute(text("SELECT * FROM pb_canonical_properties WHERE current_status='ACTIVE' ORDER BY updated_at DESC LIMIT 5000")).mappings().all()]
    exact=[];alts=[];near=[]
    for p in rows:
        ok,failed=eligibility(req,p,"STRICT",ALTS)
        if ok:
            sc,why,miss=_score(req,p,"EXACT")
            if sc>=min_score:exact.append({"property":p,"score":sc,"why":why,"missing":miss})
        else:
            ok2,_=eligibility(req,p,"ALTERNATIVE",ALTS)
            if ok2:
                sc,why,miss=_score(req,p,"ALTERNATIVE")
                if sc>=min_score:alts.append({"property":p,"score":sc,"why":why,"missing":miss})
            elif len(failed)==1:near.append({"property":p,"failed_gate":failed[0]})
    exact.sort(key=lambda x:x["score"],reverse=True);alts.sort(key=lambda x:x["score"],reverse=True)
    return {"requirement":req.model_dump(mode="json"),"exact":exact[:limit],"alternatives":alts[:limit] if len(exact)<5 else [],"near_misses":near[:20],"inventory_gap":not exact}
