from __future__ import annotations
import re

VERSION="1.0.0-SAFE-INTERPRETATION"

def _norm(v):
    return re.sub(r"\s+"," ",str(v or "").replace("\u00a0"," ")).strip()

def _list(v):
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v,list) else []

def interpret_requirement(req):
    """Read-only intelligence before deterministic Master matching. Evidence only."""
    raw=_norm(req.get("original_message"))
    tx=_norm(req.get("transaction_type")).upper()
    locations=_list(req.get("locations_list"))
    asset=_norm(req.get("property_category"))
    if tx in ("LEASE","RENT"): transaction="RENT"
    elif tx in ("SALE","PURCHASE","BUY"): transaction="SALE"
    elif re.search(r"(?i)\b(on rent|for rent|lease|rental)\b",raw): transaction="RENT"
    elif re.search(r"(?i)\b(purchase|buy|outright|for sale)\b",raw): transaction="SALE"
    else: transaction=""
    if not locations:
        known=["SANDESH VIHAR","PITAMPURA","PRASHANT VIHAR","ROHINI","PASCHIM VIHAR","PUNJABI BAGH","RAJOURI GARDEN","SAKET","GREEN PARK","HAUZ KHAS","SOUTH EXTENSION","VASANT KUNJ","DWARKA","GURUGRAM","NOIDA","PANJIM","PORVORIM","MAPUSA","SIOLIM","ASSAGAO","ANJUNA","VAGATOR","CANDOLIM","CALANGUTE","BAGA","MORJIM","MANDREM","ASHWEM"]
        up=raw.upper()
        locations=[x for x in known if re.search(r"(?<![A-Z])"+re.escape(x)+r"(?![A-Z])",up)]
    if not asset or asset.upper()=="UNKNOWN":
        rules=[("WAREHOUSE",r"(?i)\b(warehouse|godown)\b"),("HOTEL",r"(?i)\b(hotel|guest house)\b"),("RESTAURANT",r"(?i)\b(restaurant|cafe|bar\s*&?\s*restaurant)\b"),("RETAIL",r"(?i)\b(retail|shop|showroom)\b"),("OFFICE",r"(?i)\boffice\b"),("LAND",r"(?i)\b(land|plot)\b"),("VILLA",r"(?i)\b(villa|kothi|independent house)\b"),("APARTMENT",r"(?i)\b(flat|apartment|\d\s*bhk|floor)\b")]
        asset=next((name for name,pat in rules if re.search(pat,raw)),"")
    hints=[]
    if transaction: hints.append("TRANSACTION "+transaction)
    if locations: hints.append("LOCATION "+", ".join(locations))
    if asset: hints.append("PROPERTY TYPE "+asset)
    amin=req.get("area_min_sqft"); amax=req.get("area_max_sqft")
    if amin not in (None,"") or amax not in (None,""): hints.append("AREA SQFT "+str(amin or amax)+" TO "+str(amax or amin))
    enriched=raw+((" | ASTRA INTERPRETATION: "+"; ".join(hints)) if hints else "")
    confidence="HIGH" if transaction and locations else ("MEDIUM" if transaction or locations else "LOW")
    return {"version":VERSION,"enriched_text":enriched,"transaction":transaction,"locations":locations,"asset":asset,"confidence":confidence,"policy":"EVIDENCE_ONLY_NO_DB_WRITE"}
