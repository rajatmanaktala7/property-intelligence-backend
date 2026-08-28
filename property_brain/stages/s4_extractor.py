
import re
from uuid import uuid4
from ..schemas import ExtractedProperty
from ..utils import money_to_inr,phones,norm,load_json
ALIASES=load_json("location_aliases_seed.json")
def _tx(s):
    n=norm(s)
    if any(x in n for x in ("FOR SALE","SALE","SELL","OUTRIGHT","RESALE","PURCHASE")):return "SALE"
    if any(x in n for x in ("FOR RENT","RENT","LEASE","TO LET","RENTAL")):return "RENT"
def _fam(s):
    n=norm(s)
    if any(x in n for x in ("OFFICE","SHOP","SHOWROOM","RETAIL","COMMERCIAL","RESTAURANT","BANQUET","WAREHOUSE","GODOWN")):return "COMMERCIAL"
    if any(x in n for x in ("BHK","FLAT","APARTMENT","VILLA","KOTHI","PENTHOUSE","RESIDENTIAL")):return "RESIDENTIAL"
    if any(x in n for x in ("PLOT","LAND","ACRE","FARMHOUSE")):return "LAND"
def _class(s):
    n=norm(s);req=any(x in n for x in ("REQUIREMENT","LOOKING FOR","WANTED","REQUIRED","CLIENT NEED"));sup=any(x in n for x in ("AVAILABLE","FOR SALE","FOR RENT","TO LET","RESALE"))
    if req and not sup:return "REQUIREMENT"
    if sup or _tx(s):return "AVAILABILITY"
    return "AMBIGUOUS"
def _area(s):
    for p,u,mul in [(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)\b","sqft",1),(r"(?i)\b(\d{2,6}(?:\.\d+)?)\s*(?:sq\.?\s*yds?|sq\.?\s*yards?|yds?|gaj)\b","sqyd",9),(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:acre|acres)\b","acre",43560),(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(?:sqm|sq\.?\s*m)\b","sqm",10.7639)]:
        x=re.search(p,s)
        if x:
            v=float(x.group(1));return {"value":v,"unit":u,"sqft":round(v*mul,2)}
def _money(s,txn):
    pats=[]
    if txn=="RENT":pats.append(r"(?i)(?:rent|rental|lease)\s*(?:[:@=-]\s*)?₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh|k)?")
    if txn=="SALE":pats.append(r"(?i)(?:price|sale|asking|outright)\s*(?:[:@=-]\s*)?₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh|k)?")
    pats.append(r"(?i)₹\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh|k)")
    for p in pats:
        x=re.search(p,s)
        if x:return {"value":money_to_inr(float(x.group(1)),x.group(2)),"raw":x.group(0)}
def _loc(s):
    n=norm(s);hits=[(len(a),c) for a,c in ALIASES.items() if a in n]
    return sorted(hits,reverse=True)[0][1] if hits else None
def _cfg(s):
    out=[]
    for p in [r"(?i)\b\d(?:\.5)?\s*BHK(?:\s*\+?\s*(?:SER|SERVANT))?\b",r"(?i)\b\d/\d\s*BHK(?:\s*\+?\s*(?:SER|SERVANT))?\b",r"(?i)\bFULLY FURNISHED\b",r"(?i)\bSEMI FURNISHED\b",r"(?i)\bUNFURNISHED\b"]:
        x=re.search(p,s)
        if x:out.append(re.sub(r"\s+"," ",x.group(0)).strip())
    return " · ".join(dict.fromkeys(out)) or None
def extract(seg):
    tx=_tx(seg.text);f={"transaction":tx,"property_family":_fam(seg.text),"location_raw":_loc(seg.text),
      "configuration":_cfg(seg.text),"area":_area(seg.text),"money":_money(seg.text,tx),"contact_numbers":phones(seg.text),"raw_text":seg.text}
    conf={k:(.9 if v not in (None,[],{}) else 0) for k,v in f.items() if k!="raw_text"}
    present=sum(bool(f.get(k)) for k in ("transaction","property_family","location_raw","area","money"));conf["overall"]=min(.98,round(.35+present*.12,2))
    return ExtractedProperty(extraction_id=uuid4(),segment_id=seg.segment_id,raw_ids=seg.raw_ids,classification=_class(seg.text),fields=f,field_confidence=conf)
