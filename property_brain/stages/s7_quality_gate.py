
from ..schemas import GateResult
def gate(x,v,loc):
    reasons=list(v.flags)
    if x.classification=="NOISE":return GateResult(extraction_id=x.extraction_id,outcome="rejected",reasons=["noise"])
    if x.classification=="REQUIREMENT":return GateResult(extraction_id=x.extraction_id,outcome="holding",reasons=["requirement_route"])
    if any(z in v.flags for z in ("money_scale_flagged","sale_price_suspected_mislabeled_rent","area_out_of_range")):return GateResult(extraction_id=x.extraction_id,outcome="holding",reasons=reasons)
    f=x.fields
    if not loc.locality_name:reasons.append("location_missing")
    if not f.get("transaction"):reasons.append("transaction_missing")
    if sum(bool(f.get(k)) for k in ("property_family","configuration","area","money"))<2:reasons.append("low_information_fragment")
    if reasons:return GateResult(extraction_id=x.extraction_id,outcome="holding",reasons=sorted(set(reasons)))
    return GateResult(extraction_id=x.extraction_id,outcome="clean",reasons=[])
