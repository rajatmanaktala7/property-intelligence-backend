from __future__ import annotations
import re
from collections import Counter
from typing import Any, Dict, List, Optional
from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_boundary_context_mastery_v262b as v262b
import alliance_real_mastery_v262a as v262a
import alliance_property_evidence_grammar_v257b as v257b

VERSION="2.6.2C-EXCELLENCE-RELATIONSHIP-OFFER-MASTERY"
MODE="READ_ONLY_RELATIONSHIP_OFFER_CONTEXT_SHADOW"

GROUP_RE=re.compile(r"\b(?:BOTH|ALL|COMBINED|TOGETHER|PACKAGE|ENTIRE)\b",re.I)
MULTI_UNIT_RE=re.compile(r"\b(?:TOTAL\s+\d+\s*(?:SET|SETS|UNITS?)|\d+\s*(?:SET|SETS|UNITS?)\s+OF\s+\d+\s*BHK)\b",re.I)
PROPERTY_LABEL_RE=re.compile(r"\b(?:BUNGALOW|VILLA|PROPERTY|UNIT|SHOP|OFFICE|PLOT|FLAT|APARTMENT)\s*(?:NO\.?\s*)?(\d+)\b",re.I)
MONEY_RE=re.compile(r"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(CR|CRORE|L|LAC|LAKH|LACS|LAKHS|K)\b",re.I)
LOCATION_RE=re.compile(r"\b(?:DLF\s+PHASE\s+\d|SUSHANT\s+LOK\s*\d*|DWARKA|KALKAJI|SAKET|NOIDA|GURGAON|GURUGRAM|VAGATOR|ANJUNA|SIOLIM|ASSAGAO|LOKHANDWALA(?:\s+BACK\s+ROAD)?|JUHU|SECTOR\s+\d+[A-Z]?)\b",re.I)

def _norm(v:Any)->str:
    return re.sub(r"\s+"," ",str(v or "")).strip()

def _location_mentions(text:str)->List[str]:
    out=[]
    for m in LOCATION_RE.finditer(text or ""):
        x=_norm(m.group(0)).title()
        if x not in out: out.append(x)
    return out

def _group_offer(text:str)->Optional[Dict[str,Any]]:
    # "Both Bungalows: 70 Cr" must never be assigned to only the trailing bungalow.
    patterns=[
      re.compile(r"\b(BOTH\s+(?:BUNGALOWS|VILLAS|PROPERTIES|UNITS|PLOTS))\s*[:\-]?\s*(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(CR|CRORE|L|LAC|LAKH)\b",re.I),
      re.compile(r"\b(ALL\s+(?:BUNGALOWS|VILLAS|PROPERTIES|UNITS|PLOTS))\s*[:\-]?\s*(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(CR|CRORE|L|LAC|LAKH)\b",re.I)
    ]
    for p in patterns:
        m=p.search(text or "")
        if m:
            return {"scope":"MULTI_PROPERTY_PACKAGE","phrase":m.group(1),"value_token":m.group(2),"unit":m.group(3).upper()}
    return None

def coach(text:str)->Dict[str,Any]:
    base=v262b.reconstruct(text)
    entities=[dict(e) for e in base.get("entities") or []]
    raw=str(text or "")
    locs=_location_mentions(raw)
    group=_group_offer(raw)
    multi_unit=bool(MULTI_UNIT_RE.search(raw))
    lessons=[]

    # Context repair: if later child entities establish the same locality and the
    # first child is locationless, inherit only when there is exactly one credible
    # locality across the whole source message.
    known=[e.get("location") for e in entities if e.get("location")]
    unique_known=[]
    for x in known+locs:
        if x and x not in unique_known: unique_known.append(x)
    if len(unique_known)==1:
        for e in entities:
            if not e.get("location"):
                e["location"]=unique_known[0]
                e["blockers"]=[b for b in (e.get("blockers") or []) if b!="LOCATION_UNRESOLVED"]
                lessons.append("SAFE_MESSAGE_LEVEL_LOCATION_PROPAGATION")

    # Group/package offer protection.
    package_offer=None
    if group:
        package_offer=group
        lessons.append("GROUP_PRICE_DETECTED")
        # Remove matching package price from a single trailing entity.
        token=float(group["value_token"])
        factor=10000000 if group["unit"] in {"CR","CRORE"} else 100000
        target=token*factor
        for e in entities:
            vals=list(e.get("sale_values") or [])
            if target in vals:
                e["sale_values"]=[v for v in vals if v!=target]
                if e.get("transaction")=="SALE" and not e["sale_values"]:
                    e["transaction"]=None
                    e["offer_state"]="TRANSACTION_UNRESOLVED"
                    if "PACKAGE_PRICE_NOT_INDIVIDUAL_PRICE" not in e["blockers"]:
                        e["blockers"].append("PACKAGE_PRICE_NOT_INDIVIDUAL_PRICE")
                lessons.append("PACKAGE_PRICE_REMOVED_FROM_INDIVIDUAL_ENTITY")

    # Offer scope.
    for e in entities:
        if group:
            e["offer_scope"]="UNRESOLVED" if not e.get("sale_values") and not e.get("rent_values") else "PROPERTY"
        elif multi_unit:
            e["offer_scope"]="BUILDING"
        else:
            e["offer_scope"]="PROPERTY"

    relationship="MULTI_PROPERTY" if len(entities)>1 else ("MULTI_UNIT_BUILDING" if multi_unit else "SINGLE_PROPERTY")
    if group: relationship="MULTI_PROPERTY_WITH_PACKAGE_OFFER"

    # One physical property may legitimately have rent and sale simultaneously.
    dual_offer_single=bool(
        len(entities)==1 and
        entities[0].get("offer_state")=="MULTIPLE_OFFERS_UNRESOLVED" and
        not multi_unit
    )
    if dual_offer_single:
        lessons.append("DUAL_OFFER_SINGLE_PROPERTY_PRESERVED")
        entities[0]["offer_scope"]="PROPERTY"

    # Multi-unit building is one physical asset until evidence supports unit-level
    # physical identities. Preserve both offers without fabricating units.
    if multi_unit and len(entities)==1:
        lessons.append("MULTI_UNIT_BUILDING_PRESERVED_AS_ONE_ASSET")

    unresolved=[]
    for i,e in enumerate(entities):
        blockers=set(e.get("blockers") or [])
        if not e.get("location"): unresolved.append(f"ENTITY_{i+1}_LOCATION")
        if "BOUNDARY_REVIEW_REQUIRED" in blockers: unresolved.append(f"ENTITY_{i+1}_BOUNDARY")
        if "PACKAGE_PRICE_NOT_INDIVIDUAL_PRICE" in blockers: unresolved.append(f"ENTITY_{i+1}_PACKAGE_PRICE")
        if e.get("transaction") is None and e.get("offer_state")=="TRANSACTION_UNRESOLVED":
            unresolved.append(f"ENTITY_{i+1}_TRANSACTION")

    if group: unresolved.append("PACKAGE_OFFER_REQUIRES_RELATIONSHIP_REVIEW")
    if multi_unit and len(entities)==1 and entities[0].get("offer_state")=="MULTIPLE_OFFERS_UNRESOLVED":
        unresolved.append("MULTI_UNIT_DUAL_OFFER_REQUIRES_OFFER_REVIEW")

    safe=bool(entities and not unresolved)
    return {
      "relationship_type":relationship,
      "entity_count":len(entities),
      "entities":entities,
      "package_offer":package_offer,
      "multi_unit_signal":multi_unit,
      "lessons":sorted(set(lessons)),
      "unresolved":sorted(set(unresolved)),
      "safe_auto_split":safe,
      "safe_for_controlled_write_shadow":safe,
      "canonical_writes":0,"offer_writes":0,"database_write":False
    }

CASES=[
 ("LOKHANDWALA_LOCATION_BACKPROP",
  "6250 Carpet | 4 BHK | 35 Cr | LOKHANDWALA BACK ROAD BUNGALOW 1 | 3854 Carpet | 5 BHK | LOKHANDWALA BACK ROAD BUNGALOW 2 | 2656 Carpet | 6 BHK | Both Bungalows: 70 Cr",
  lambda r:r["entity_count"]==3 and all(e.get("location")=="Lokhandwala Back Road" for e in r["entities"])),
 ("PACKAGE_PRICE_NOT_TRAILING_PROPERTY",
  "LOKHANDWALA BACK ROAD | BUNGALOW 1 | 3854 Carpet | 5 BHK | BUNGALOW 2 | 2656 Carpet | 6 BHK | Both Bungalows: 70 Cr",
  lambda r:r["package_offer"] is not None and r["package_offer"]["scope"]=="MULTI_PROPERTY_PACKAGE" and all(700000000.0 not in (e.get("sale_values") or []) for e in r["entities"])),
 ("PACKAGE_NOT_SAFE_FOR_WRITE",
  "LOKHANDWALA BACK ROAD | BUNGALOW 1 | 3854 Carpet | 5 BHK | BUNGALOW 2 | 2656 Carpet | 6 BHK | Both Bungalows: 70 Cr",
  lambda r:not r["safe_for_controlled_write_shadow"]),
 ("MULTI_UNIT_BUILDING_DUAL_OFFERS",
  "2bhk 2set each story | Total 8 set of 2bhk | With lift Rent 2 lac | 130 sqyd plot size | Demand 3.80 cr | New Pg",
  lambda r:r["entity_count"]==1 and r["relationship_type"]=="MULTI_UNIT_BUILDING" and r["entities"][0]["offer_scope"]=="BUILDING" and not r["safe_for_controlled_write_shadow"]),
 ("SINGLE_PROPERTY_DUAL_OFFER",
  "SAKET | 3000 SQFT | Available for Rent 5 Lakh or Sale 8 Cr",
  lambda r:r["entity_count"]==1 and r["relationship_type"]=="SINGLE_PROPERTY" and "DUAL_OFFER_SINGLE_PROPERTY_PRESERVED" in r["lessons"]),
 ("NORMAL_RENT_STILL_SAFE",
  "DLF PHASE 2 | 4 BHK | 400 SYDS | Fully Furnished | Rent 1.60 Lac",
  lambda r:r["entity_count"]==1 and r["entities"][0]["transaction"]=="RENT" and r["safe_for_controlled_write_shadow"]),
 ("REQUIREMENT_NOT_CONVERTED",
  "Looking for 3000 SQFT in Saket for restaurant budget 5 Lakh",
  lambda r:r["entity_count"]==1 and r["entities"][0]["classification"]=="REQUIREMENT"),
]

def regression()->Dict[str,Any]:
    out=[]
    for key,text,fn in CASES:
        r=coach(text); ok=bool(fn(r)); out.append({"case_key":key,"passed":ok,"result":r})
    p=sum(1 for x in out if x["passed"]); t=len(out)
    return {"status":"PASS" if p==t else "FAIL","version":VERSION,"total":t,"passed":p,"failed":t-p,
            "score":round(100*p/t,2),"critical_failures":t-p,"results":out,"writes_performed":0}

def real_exam(engine,limit:int=500)->Dict[str,Any]:
    b=v257b._benchmark(engine,limit)
    stats=Counter(); examples=[]; total=0
    for burst in b.get("bursts") or []:
      for c in burst.get("candidates") or []:
        total+=1; raw=str(c.get("own_text_redacted") or ""); r=coach(raw)
        stats["REL_"+r["relationship_type"]]+=1
        if r["package_offer"]: stats["PACKAGE_OFFERS"]+=1
        if r["multi_unit_signal"]: stats["MULTI_UNIT_SIGNALS"]+=1
        if r["safe_for_controlled_write_shadow"]: stats["SAFE_SHADOW"]+=1
        else: stats["REVIEW_SHADOW"]+=1
        for lesson in r["lessons"]: stats["LESSON_"+lesson]+=1
        for issue in r["unresolved"]: stats["UNRESOLVED_"+issue.split("_",2)[-1]]+=1
        upstream=((c.get("v257b") or {}).get("record_integrity") or {}).get("class")
        if (upstream=="MULTIPLE_PROPERTIES_OR_MERGED" or r["package_offer"] or r["multi_unit_signal"]) and len(examples)<100:
          examples.append({"raw":raw,"upstream_integrity":upstream,"coached":r})
    reg=regression(); a=v262a.regression(); breg=v262b.regression()
    gate=bool(reg["critical_failures"]==0 and a["critical_failures"]==0 and breg["critical_failures"]==0)
    return {"status":"PASS" if gate else "TRAINING_REQUIRED","version":VERSION,"requested_limit":limit,
            "real_candidates_examined":total,"stats":dict(stats),"examples":examples,
            "v262c_score":reg["score"],"v262c_critical_failures":reg["critical_failures"],
            "v262b_score":breg["score"],"v262b_critical_failures":breg["critical_failures"],
            "v262a_score":a["score"],"v262a_critical_failures":a["critical_failures"],
            "mastery_gate_passed":gate,"read_only":True,
            "canonical_writes":0,"offer_writes":0,"matcher_writes":0,"whatsapp_live_writes":0,
            "claim":"PASS means all known V262A/B/C safety lessons pass. It does not mean universal real-world accuracy."}

def register(core):
    app=core.app; engine=core.engine; route="/api/v7/property-ai/mastery-v262c/status"
    if any(getattr(r,"path",None)==route for r in app.router.routes):
        return {"status":"ALREADY_REGISTERED","version":VERSION,"route":route}
    @app.get(route)
    def status():
        return JSONResponse({"status":"READY","version":VERSION,"mode":MODE,"read_only":True,
                             "canonical_writes":0,"offer_writes":0,"matcher_modified":False,"whatsapp_live_modified":False})
    @app.get("/api/v7/property-ai/mastery-v262c/regression")
    def reg(): return JSONResponse(regression())
    @app.get("/api/v7/property-ai/mastery-v262c/exam")
    def exam(limit:int=Query(500,ge=1,le=1000)): return JSONResponse(real_exam(engine,limit))
    return {"status":"REGISTERED","version":VERSION,"route":route,
            "regression":"/api/v7/property-ai/mastery-v262c/regression",
            "exam":"/api/v7/property-ai/mastery-v262c/exam?limit=500"}
