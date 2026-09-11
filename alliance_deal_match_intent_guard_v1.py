from __future__ import annotations
import re
from typing import Any, Dict

VERSION = "1.0.0-DEAL-MATCH-INTENT-GUARD"

DEMAND = (
    (r"\bLOOKING\s+FOR\b", 50, "looking_for"),
    (r"\bREQUIREMENT\b", 48, "requirement"),
    (r"\bREQUIRED\b", 45, "required"),
    (r"\bNEED(?:ED)?\b", 42, "need"),
    (r"\bWANTED\b", 42, "wanted"),
    (r"\bSEEKING\b", 40, "seeking"),
    (r"\bCLIENT\s+(?:IS\s+)?LOOKING\b", 55, "client_looking"),
    (r"\bFOR\s+OUR\s+CLIENT\b", 45, "for_our_client"),
)
SUPPLY = (
    (r"\bPROPERTY\s+AVAILABLE\b", 55, "property_available"),
    (r"\bAVAILABLE\s+FOR\s+(?:SALE|RENT|LEASE)\b", 55, "available_for"),
    (r"\bFOR\s+SALE\b", 35, "for_sale"),
    (r"\bFOR\s+RENT\b", 35, "for_rent"),
    (r"\bFOR\s+LEASE\b", 35, "for_lease"),
    (r"\bTOTAL\s+DEAL\s+VALUE\b", 55, "total_deal_value"),
    (r"\bCHEQUE\s+FLEXIBLE\b", 32, "cheque_flexible"),
    (r"\bCOVERED\s+CAR\s+PARKING\b", 18, "covered_parking"),
    (r"\b(?:BASEMENT|TERRACE)\s+INCLUDED\b", 22, "included_component"),
)

def _norm(v: Any) -> str:
    s = re.sub(r"[^A-Z0-9₹./+\- |]+", " ", str(v or "").upper())
    return re.sub(r"\s+", " ", s).strip()

def classify(raw: str) -> Dict[str, Any]:
    t = _norm(raw)
    ds = ss = 0
    dh, sh = [], []
    for p,w,l in DEMAND:
        if re.search(p,t,re.I):
            ds += w; dh.append(l)
    for p,w,l in SUPPLY:
        if re.search(p,t,re.I):
            ss += w; sh.append(l)

    facts = {
        "area": bool(re.search(r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|YDS?|SQM|SQ\.?\s*M)\b", t, re.I)),
        "rate_psf": bool(re.search(r"(?:₹|RS\.?|INR)?\s*\d[\d,]*(?:\.\d+)?\s*(?:/-)?\s*(?:PER\s+SQ\.?\s*FT|PSF|/\s*SQ\.?\s*FT)", t, re.I)),
        "total_price": bool(re.search(r"(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|LAKH|LAKHS|LAC)\b", t, re.I)),
        "floor": bool(re.search(r"\b(?:GROUND|LOWER\s+GROUND|UPPER\s+GROUND|1ST|2ND|3RD|4TH|5TH|\d+(?:ST|ND|RD|TH))\s+FLOOR\b", t, re.I)),
        "facing": bool(re.search(r"\b(?:NORTH|SOUTH|EAST|WEST|ANANTRAJ)[ -]FACING\b|\bFACING\b", t, re.I)),
        "unit": bool(re.search(r"\b\d+(?:\.\d+)?\s*BHK\b|\b(?:HIGH\s+STREET\s+)?RETAIL\b|\bSHOP\b|\bOFFICE\b|\bVILLA\b|\bPLOT\b", t, re.I)),
        "project_header": bool(re.search(r"\b[A-Z][A-Z0-9 ]{3,}\s*\|\s*(?:SEC|SECTOR)\s*-?\s*\d{1,3}[A-Z]?\b", t)),
    }
    n = sum(facts.values())
    if facts["rate_psf"] and facts["total_price"]:
        ss += 45; sh.append("rate_plus_total_price")
    if facts["project_header"] and facts["area"] and facts["unit"]:
        ss += 38; sh.append("project_unit_area_listing_structure")
    if n >= 5:
        ss += 30; sh.append("dense_asset_facts")
    elif n >= 4:
        ss += 20; sh.append("multiple_asset_facts")

    if ds >= 45 and ds >= ss + 15:
        role = "REQUIREMENT"; conf = min(99, 78 + min(21, (ds-ss)//3))
    elif ss >= 55 and ss >= ds + 20:
        role = "SUPPLY"; conf = min(99, 80 + min(19, (ss-ds)//4))
    else:
        role = "AMBIGUOUS"; conf = 45 if (ds or ss) else 0

    return {"version":VERSION,"role":role,"confidence":int(conf),
            "demand_score":int(ds),"supply_score":int(ss),
            "demand_hits":dh,"supply_hits":sh,
            "structured_listing_facts":{**facts,"count":n}}

def install(core):
    import alliance_deal_match_ai_v60 as v60
    if getattr(v60, "_INTENT_GUARD_INSTALLED", False):
        return {"status":"ALREADY_INSTALLED","version":VERSION,"matcher_version":v60.VERSION}

    original_render = v60.render_results
    original_multi = v60.run_match_multi

    def guarded_render(core_arg, q, mode, min_score):
        parts = v60.split_requirement_text(q)
        decisions = [classify(p["source"]) for p in parts]
        if parts and all(d["role"] == "REQUIREMENT" for d in decisions):
            return original_render(core_arg, q, mode, min_score)

        cards = ['<div class="card"><h2>WhatsApp Intent Intelligence</h2>'
                 '<p class="green">Only genuine REQUIREMENT items may enter Matcher V6.6.0.</p></div>']
        for p,d in zip(parts,decisions):
            src = v60.phase5.sanitize_text(p["source"])
            if d["role"] == "SUPPLY":
                cards.append(
                    '<div class="card"><h2>Item '+v60.esc(p["number"])+' · PROPERTY LISTING</h2>'
                    '<p>'+v60.esc(src)+'</p>'
                    '<p class="amber"><b>Not sent to Requirement Matcher.</b> This is supply/inventory.</p>'
                    '<p><b>Confidence:</b> '+v60.esc(d["confidence"])+'% · <b>Evidence:</b> '
                    +v60.esc(", ".join(d["supply_hits"]))+'</p>'
                    '<a class="btn" href="/property-manual">Open Property Intake</a> '
                    '<a class="btn" href="/alliance/primary/availability">Availability Verification</a></div>'
                )
            elif d["role"] == "AMBIGUOUS":
                cards.append(
                    '<div class="card"><h2>Item '+v60.esc(p["number"])+' · NEEDS HUMAN CLASSIFICATION</h2>'
                    '<p>'+v60.esc(src)+'</p>'
                    '<p class="red"><b>Matcher blocked for safety.</b></p>'
                    '<p>Requirement score: '+v60.esc(d["demand_score"])+' · Supply score: '
                    +v60.esc(d["supply_score"])+'</p></div>'
                )
            else:
                res = v60.run_match(core_arg, p["normalized"], mode, min_score, 100)
                s = res["summary"]
                cards.append(
                    '<div class="card"><h2>Item '+v60.esc(p["number"])+' · REQUIREMENT</h2>'
                    '<p>'+v60.esc(src)+'</p>'
                    '<p><b>Matches:</b> '+v60.esc(s.get("exact_verified"))+' verified · '
                    +v60.esc(s.get("exact_needs_verification"))+' verify first · '
                    +v60.esc(s.get("approved_alternatives"))+' alternatives</p></div>'
                )
        cards.append('<div class="card"><p><b>Intent Guard:</b> '+v60.esc(VERSION)
                     +' · <b>Matcher:</b> '+v60.esc(v60.VERSION)
                     +'</p><p class="green">Contacts remain hidden. Supply is never auto-verified.</p>'
                     '<a class="btn" href="/deal-match-ai-v60">Run Another Item</a></div>')
        return v60.HTMLResponse(v60._page("Alliance Deal Match AI · Intent Guard","".join(cards)))

    def guarded_multi(core_arg, q, mode="SMART", min_score=70.0, limit=100):
        parts = v60.split_requirement_text(q)
        out = []
        for p in parts:
            d = classify(p["source"])
            item = {"item_number":p["number"],"intent":d}
            if d["role"] == "REQUIREMENT":
                item["matched"] = True
                item["result"] = v60.run_match(core_arg,p["normalized"],mode,min_score,limit)
            else:
                item["matched"] = False
                item["route_to"] = "PROPERTY_INTAKE" if d["role"]=="SUPPLY" else "HUMAN_CLASSIFICATION"
                item["source"] = v60.phase5.sanitize_text(p["source"])
            out.append(item)
        return {"version":v60.VERSION,"engine_version":v60.ENGINE_VERSION,
                "intent_guard_version":VERSION,"item_count":len(parts),
                "results":out,"contacts_exposed":False}

    v60.render_results = guarded_render
    v60.run_match_multi = guarded_multi
    v60._INTENT_GUARD_INSTALLED = True
    return {"status":"INSTALLED","version":VERSION,"matcher_version":v60.VERSION,
            "matcher_file_modified":False,"blocks_supply_from_matcher":True}

def regression():
    cases = [
      ("SMARTWORLD ORCHARD | SEC 61 3.5 BHK | 1,680 Sq. Ft. North-Facing Entry East & West-Facing Balconies ₹15,000/- per Sq. Ft. Total Deal Value: ₹2.52 Cr. Cheque Flexible 1/4 Basement & 1/4 Terrace Included","SUPPLY"),
      ("AIPL JOY SQUARE | SEC 63A High Street Retail | 1st Floor 415 Sq. Ft. Anantraj Facing ₹15,000/- per Sq. Ft. Total Deal Value: ₹62.25 Lakhs 1 Covered Car Parking Including","SUPPLY"),
      ("Looking for a 3bhk fully furnished Villa in a gated complex for long term rent in and around Anjuna, Vagator, Assagao, Parra, Saligao and Siolim. Budget 1.3L","REQUIREMENT"),
      ("Requirement of 400 to 550 sqyd Plot in Experion Westerlies Sector 108 Only in Block-A","REQUIREMENT"),
    ]
    ans=[]
    for raw,expected in cases:
        got=classify(raw)
        assert got["role"]==expected,(expected,got)
        ans.append((expected,got["confidence"],got["demand_score"],got["supply_score"]))
    return ans
