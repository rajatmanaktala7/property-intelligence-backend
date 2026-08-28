from __future__ import annotations
import re

VERSION="6.2-RECONSTRUCTED-INVENTORY-ACCURACY"
_INSTALLED=False
_ORIGINAL_ELIGIBILITY=None
_ORIGINAL_WA=None

GENERIC_BAD={
    "PROPERTY","PROPERTY AVAILABLE","AVAILABLE PROPERTY","AVAILABLE","DETAILS AVAILABLE",
    "CONTACT FOR DETAILS","PLEASE CALL","CALL FOR DETAILS","MORE DETAILS","UNKNOWN",
    "PROPERTY AVAILABILITY","RENT PROPERTY","SALE PROPERTY"
}

def _norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def meaningful_candidate(p):
    desc=str(p.get("description") or "").strip()
    n=_norm(desc)
    if not n or n in GENERIC_BAD or len(n)<18:
        return False
    anchors=0
    if p.get("location"): anchors+=1
    if p.get("transaction"): anchors+=1
    if p.get("family"): anchors+=1
    if p.get("area") is not None: anchors+=1
    if p.get("price") is not None: anchors+=1
    if p.get("subtype"): anchors+=1
    if any(x in n for x in (
        "BHK","SQFT","SQ FT","SQ YD","APARTMENT","FLAT","VILLA","FLOOR","SHOP",
        "SHOWROOM","OFFICE","RESTAURANT","BANQUET","PLOT","LAND","WAREHOUSE",
        "GODOWN","HOTEL","PENTHOUSE"
    )):
        anchors+=1
    if "GIRJA" in n and anchors<3:
        return False
    return anchors>=2

def install():
    global _INSTALLED,_ORIGINAL_ELIGIBILITY,_ORIGINAL_WA
    import alliance_deal_match_ai_v60 as v60

    if _INSTALLED:
        return {"status":"OK","version":VERSION,"installed":True,"already_installed":True}

    _ORIGINAL_ELIGIBILITY=v60.eligibility
    _ORIGINAL_WA=v60._wa_master_candidates

    def wa_filtered(core,limit=5000):
        rows=_ORIGINAL_WA(core,limit)
        out=[]
        try:
            import alliance_live_feed_purity as wa_ui
        except Exception:
            wa_ui=None
        for c in rows:
            p=v60.normalize_candidate(c)
            if not meaningful_candidate(p):
                continue
            if wa_ui is None:
                out.append(c);continue
            seed={
                "description":c.get("description"),"raw_text":c.get("description"),
                "location":p.get("location"),"transaction":p.get("transaction"),
                "property_type":p.get("family") or c.get("property_type_raw"),
                "area":c.get("area"),"price":c.get("price"),
                "contact_number":c.get("contact"),"source":c.get("source_name")
            }
            children=wa_ui.split_multi_property(seed)
            for child in children:
                cc=dict(c)
                cc["description"]=child.get("clean_description") or child.get("description") or c.get("description")
                cc["location_raw"]=child.get("location") or c.get("location_raw")
                cc["transaction_raw"]=child.get("transaction") or c.get("transaction_raw")
                cc["property_type_raw"]=child.get("property_type") or c.get("property_type_raw")
                cc["area"]=v60.to_float(child.get("area")) or c.get("area")
                if child.get("price") not in (None,""): cc["price"]=v60.money_value(child.get("price"))
                out.append(cc)
        return out

    def eligibility_v61(req,p,allow_nearby=False):
        ok,code,reasons=_ORIGINAL_ELIGIBILITY(req,p,allow_nearby)
        if not ok:
            return ok,code,reasons

        if not meaningful_candidate(p):
            return False,"LOW_QUALITY_CANDIDATE",["Candidate lacks usable property identity"]

        # Explicit intended use: unknown subtype is no longer allowed unless the raw
        # candidate text explicitly proves suitability.
        rsub=req.get("subtype")
        psub=p.get("subtype")
        if rsub and not psub:
            blob=_norm(" ".join(str(p.get(k) or "") for k in ("description","suitable_for","blob")))
            if _norm(rsub) not in blob:
                return False,"SUBTYPE_UNKNOWN",[f"Required subtype {rsub}; candidate suitability not proven"]

        # Severe area mismatch is a hard rejection. Small deviations remain scoreable.
        amin,amax=req.get("area_min"),req.get("area_max")
        area=p.get("area")
        if area is not None and (amin is not None or amax is not None):
            lo=amin if amin is not None else amax
            hi=amax if amax is not None else amin
            if lo and hi:
                if area < lo*0.60 or area > hi*1.40:
                    return False,"WRONG_AREA",[f"Required approx {lo:.0f}-{hi:.0f}; candidate {area:.0f}"]

        # More than 25% above a known max budget should not rank as a plausible match.
        bmax=req.get("budget_max")
        price=p.get("price")
        if bmax and price is not None and price>bmax*1.25:
            return False,"TOO_EXPENSIVE",[f"Budget max {bmax:.0f}; candidate {price:.0f}"]

        return ok,code,reasons

    v60._wa_master_candidates=wa_filtered
    v60.eligibility=eligibility_v61
    v60.VERSION="6.1.0-ALLIANCE-DEAL-MATCH-AI-ACCURACY"

    _INSTALLED=True
    return {
        "status":"OK",
        "version":VERSION,
        "installed":True,
        "changes":[
            "low-quality WhatsApp candidate rejection",
            "explicit subtype proof gate",
            "severe area mismatch hard rejection",
            "budget >25% hard rejection"
        ],
        "route_unchanged":"/deal-match-ai-v60",
        "database_mutation":False
    }
