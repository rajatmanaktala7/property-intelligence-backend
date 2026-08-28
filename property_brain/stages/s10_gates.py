
def eligibility(req,p,mode="STRICT",alternatives=None):
    failed=[];loc=p.get("locality");allowed=req.acceptable_locations or ([req.locality] if req.locality else [])
    if req.locality:
        if mode=="STRICT" and loc not in allowed:failed.append("location")
        if mode!="STRICT" and loc not in allowed and loc not in (alternatives or {}).get(req.locality,[]):failed.append("location")
    if req.transaction and p.get("transaction_type")!=req.transaction:failed.append("transaction")
    if req.property_family and p.get("property_family")!=req.property_family:failed.append("property_type")
    if req.area_min_sqft and p.get("area_sqft") is not None and (p["area_sqft"]<req.area_min_sqft*.6 or p["area_sqft"]>req.area_max_sqft*1.4):failed.append("area")
    price=p.get("rent_value") if req.transaction=="RENT" else p.get("sale_price_value")
    if req.budget_max and price is not None and price>req.budget_max*1.25:failed.append("budget")
    return not failed,failed
