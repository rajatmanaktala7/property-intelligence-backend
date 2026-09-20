from __future__ import annotations
import re

VERSION="1.4.0-EVIDENCE-FIRST-REQUIREMENT-REPAIR"

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
    # Raw source evidence wins over stale/incorrect structured gate fields.
    # This is read-only: it repairs interpretation for matching without rewriting history.
    if re.search(r"(?i)\b(required|requirement|wanted|looking for|need)\b[\s\S]{0,120}\b(purchase|buy|outright)\b|\b(for purchase|to purchase|outright purchase)\b",raw):
        transaction="SALE"
    elif re.search(r"(?i)\b(required|requirement|wanted|looking for|need)\b[\s\S]{0,120}\b(on rent|for rent|lease|rental)\b|\b(for lease|on lease|long[ -]?term rental)\b",raw):
        transaction="RENT"
    elif tx in ("LEASE","RENT"): transaction="RENT"
    elif tx in ("SALE","PURCHASE","BUY"): transaction="SALE"
    elif re.search(r"(?i)\b(on rent|for rent|lease|rental)\b",raw): transaction="RENT"
    elif re.search(r"(?i)\b(purchase|buy|outright|for sale)\b",raw): transaction="SALE"
    else: transaction=""
    if not locations:
        known=["SANDESH VIHAR","PITAMPURA","PRASHANT VIHAR","ROHINI","PASCHIM VIHAR","PUNJABI BAGH","RAJOURI GARDEN","SAKET","GREEN PARK","HAUZ KHAS","SOUTH EXTENSION","VASANT KUNJ","DWARKA","GURUGRAM","NOIDA","PANJIM","PORVORIM","MAPUSA","SIOLIM","ASSAGAO","ANJUNA","VAGATOR","CANDOLIM","CALANGUTE","BAGA","MORJIM","MANDREM","ASHWEM"]
        up=raw.upper()
        locations=[x for x in known if re.search(r"(?<![A-Z])"+re.escape(x)+r"(?![A-Z])",up)]
    # Explicit source wording also overrides obviously generic/misclassified gate assets.
    evidence_rules=[("BANQUET",r"(?i)\b(banquet|wedding venue|marriage hall|party lawn|wedding lawn|farmhouse)\b"),("WAREHOUSE",r"(?i)\b(warehouse|godown)\b"),("HOTEL",r"(?i)\b(hotel|guest house)\b"),("RESTAURANT",r"(?i)\b(restaurant|cafe|bar\s*&?\s*restaurant)\b"),("RETAIL",r"(?i)\b(retail|shop|showroom)\b"),("OFFICE",r"(?i)\boffice\b"),("LAND",r"(?i)\b(land|plot)\b"),("VILLA",r"(?i)\b(villa|kothi|independent house)\b"),("APARTMENT",r"(?i)\b(flat|apartment|\d\s*bhk|floor)\b")]
    evidence_asset=next((name for name,pat in evidence_rules if re.search(pat,raw)),"")
    if evidence_asset and (not asset or asset.upper() in {"UNKNOWN","COMMERCIAL"}):
        asset=evidence_asset
    # Generic COMMERCIAL in the gate is a family, not a useful subtype. Recover
    # explicit venue intent from the requirement text without changing stored data.
    if asset.upper()=="COMMERCIAL":
        venue_rules=[("BANQUET",r"(?i)\b(banquet|wedding venue|marriage hall|party lawn|wedding lawn|farmhouse)\b"),("RESTAURANT",r"(?i)\b(restaurant|cafe|bar\s*&?\s*restaurant)\b"),("RETAIL",r"(?i)\b(retail|shop|showroom)\b"),("OFFICE",r"(?i)\boffice\b"),("HOTEL",r"(?i)\b(hotel|guest house)\b")]
        asset=next((name for name,pat in venue_rules if re.search(pat,raw)),asset)
    hints=[]
    if transaction: hints.append("TRANSACTION "+transaction)
    if locations: hints.append("LOCATION "+", ".join(locations))
    if asset: hints.append("PROPERTY TYPE "+asset)
    # Preserve explicit bedroom range and commercial-use intent for the canonical parser.
    bhk=re.search(r"(?i)\b(\d+)\s*[-–—]\s*(\d+)\s*BHK\b",raw)
    if bhk:
        hints.append("BHK "+bhk.group(1)+" TO "+bhk.group(2))
    if re.search(r"(?i)\b(commercial use|airbnb|short[ -]?term rental|homestay)\b",raw):
        hints.append("INTENDED USE COMMERCIAL HOSPITALITY")
    # Budget wording such as "2 lacs" is explicit evidence and must survive.
    budget=re.search(r"(?i)\bbudget\s*[:\-]?\s*(?:around|approx(?:imately)?|upto|up to)?\s*(\d+(?:\.\d+)?)\s*(cr|crore|crores|lac|lakh|lakhs|k)\b",raw)
    if budget:
        hints.append("BUDGET "+budget.group(1)+" "+budget.group(2))
    amin=req.get("area_min_sqft"); amax=req.get("area_max_sqft")
    if amin not in (None,"") or amax not in (None,""): hints.append("AREA SQFT "+str(amin or amax)+" TO "+str(amax or amin))
    enriched=raw+((" | ASTRA INTERPRETATION: "+"; ".join(hints)) if hints else "")
    confidence="HIGH" if transaction and locations else ("MEDIUM" if transaction or locations else "LOW")
    return {"version":VERSION,"enriched_text":enriched,"transaction":transaction,"locations":locations,"asset":asset,"area_min_sqft":amin,"area_max_sqft":amax,"confidence":confidence,"policy":"EVIDENCE_ONLY_NO_DB_WRITE"}
