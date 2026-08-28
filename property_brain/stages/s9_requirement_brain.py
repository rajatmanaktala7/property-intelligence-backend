
import re
from uuid import uuid4
from ..schemas import Requirement
from ..utils import money_to_inr,phones,norm,load_json
A=load_json("location_aliases_seed.json")
def parse_requirement(raw):
    n=norm(raw);tx="RENT" if any(x in n for x in ("RENT","LEASE","TO LET")) else ("SALE" if any(x in n for x in ("SALE","BUY","PURCHASE","OUTRIGHT")) else None)
    fam="COMMERCIAL" if any(x in n for x in ("COMMERCIAL","OFFICE","SHOP","SHOWROOM","RETAIL","RESTAURANT","BANQUET","WAREHOUSE")) else ("RESIDENTIAL" if any(x in n for x in ("BHK","FLAT","APARTMENT","VILLA","RESIDENTIAL")) else ("LAND" if any(x in n for x in ("LAND","PLOT")) else None))
    use=next((u for u in ("RESTAURANT","BANQUET","OFFICE","RETAIL","SHOWROOM","WAREHOUSE","CAFE","LOUNGE","HOTEL") if u in n),None)
    locs=[]
    for a,c in A.items():
        if a in n and c not in locs:locs.append(c)
    amin=amax=None;m=re.search(r"(?i)\b(\d{2,7})\s*(?:-|to|–)\s*(\d{2,7})\s*(?:sqft|sq\.?\s*ft|sft)?",raw)
    if m:amin,amax=sorted([float(m.group(1)),float(m.group(2))])
    else:
        m=re.search(r"(?i)\b(\d{2,7})\s*(?:sqft|sq\.?\s*ft|sft)\b",raw)
        if m:amin,amax=float(m.group(1))*.9,float(m.group(1))*1.1
    vals=[money_to_inr(float(nm),u) for nm,u in re.findall(r"(?i)₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh|k)\b",raw)]
    bmin,bmax=(min(vals),max(vals)) if len(vals)>=2 else ((None,vals[0]) if vals else (None,None))
    conf=min(.98,round(.35+sum(bool(x) for x in (tx,fam,locs,amin,bmax))*.12,2))
    return Requirement(requirement_id=uuid4(),raw_text=raw,transaction=tx,property_family=fam,intended_use=use,locality=locs[0] if locs else None,acceptable_locations=locs,area_min_sqft=amin,area_max_sqft=amax,budget_min=bmin,budget_max=bmax,contact_numbers=phones(raw),confidence=conf)
