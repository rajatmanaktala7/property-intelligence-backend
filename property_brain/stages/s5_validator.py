
from ..schemas import ValidationResult
from ..utils import load_json,norm
B=load_json("money_bands.json")["default"];BLOCK={"money_scale_flagged","sale_price_suspected_mislabeled_rent","area_out_of_range","requirement_not_inventory"}
def validate(x):
    f=x.fields;flags=[];tx=f.get("transaction");fam=f.get("property_family");money=f.get("money");area=f.get("area");raw=norm(f.get("raw_text"))
    if x.classification=="REQUIREMENT":flags.append("requirement_not_inventory")
    if money and tx=="RENT":
        ceil=B["commercial_monthly_rent_max"] if fam=="COMMERCIAL" else B["residential_monthly_rent_max"]
        if money["value"]>ceil:flags.append("money_scale_flagged")
        if (" CR " in f" {raw} " or " CRORE " in f" {raw} ") and not any(q in raw for q in ("PER MONTH","MONTHLY","/MONTH")):flags.append("sale_price_suspected_mislabeled_rent")
    if area and (area["sqft"]<100 or area["sqft"]>5_000_000):flags.append("area_out_of_range")
    if not tx:flags.append("transaction_missing")
    if not fam:flags.append("property_type_missing")
    return ValidationResult(extraction_id=x.extraction_id,passed=not any(k in BLOCK for k in flags),flags=flags,corrected_fields={})
