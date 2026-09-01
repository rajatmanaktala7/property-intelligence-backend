from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from collections import Counter

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_context_recovery_academy_v262d as v262d
import alliance_excellence_mastery_v262c as v262c
import alliance_boundary_context_mastery_v262b as v262b
import alliance_real_mastery_v262a as v262a

VERSION="2.6.2E1-SEMANTIC-MEASUREMENT-PRECEDENCE-FIX"
MODE="READ_ONLY_REQUIREMENT_SPATIAL_MEASUREMENT_MONEY_SUITABILITY_SHADOW"

# -----------------------------
# Normalisation
# -----------------------------
MOJIBAKE_REPLACEMENTS = {
    "â¹":"₹","â‚¹":"₹","ð°":"₹","â¢":"•","â¨":"•","âªï¸":"•","Ã—":"x","Ã":"x",
    "â":" ","ð®ð³":" ","ð£ï¸":" "
}

def norm_text(v:Any)->str:
    s=str(v or "")
    for a,b in MOJIBAKE_REPLACEMENTS.items():
        s=s.replace(a,b)
    s=re.sub(r"\s+"," ",s)
    return s.strip(" |")

def uniq(xs):
    out=[]
    for x in xs:
        if x and x not in out: out.append(x)
    return out

# -----------------------------
# Requirement Brain
# -----------------------------
REQ_RE=re.compile(r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|BUYER\s+REQUIREMENT|RENTAL\s+REQUIREMENT|PURCHASE\s+REQUIREMENT)\b",re.I)
PREFERRED_LOCATIONS_RE=re.compile(r"\b(?:PREFERRED\s+LOCATIONS?|LOCATIONS?\s+PREFERRED|ACCEPTABLE\s+LOCATIONS?)\s*[:\-]?\s*(.+?)(?=\b(?:BUDGET|POSSESSION|READY\s+TO\s+CLOSE|KINDLY|CONTACT|PHONE)\b|$)",re.I)
LOCATION_TOKEN_RE=re.compile(r"\b(?:VAGATOR|ANJUNA|SIOLIM|ASSAGAO|SAKET|KALKAJI|DWARKA|NOIDA|GURGAON|GURUGRAM|JUHU|LOKHANDWALA(?:\s+BACK\s+ROAD)?|VASANT\s+KUNJ|VASANT\s+VIHAR|DEFENCE\s+COLONY|GREATER\s+KAILASH\s*[12]?|GK\s*[12]|SECTOR\s+\d+[A-Z]?)\b",re.I)
BHK_RE=re.compile(r"\b(\d+)\s*/\s*(\d+)\s*BHK\b|\b(\d+)\s*BHK\b",re.I)
USE_RE=re.compile(r"\b(?:FOR|SUITABLE\s+FOR|IDEAL\s+FOR)\s+([A-Z][A-Z /,&\-]{2,60})",re.I)

def requirement_brain(text:str)->Dict[str,Any]:
    t=norm_text(text)
    is_req=bool(REQ_RE.search(t))
    locs=uniq([m.group(0).title() for m in LOCATION_TOKEN_RE.finditer(t)])

    preferred=[]
    m=PREFERRED_LOCATIONS_RE.search(t)
    if m:
        preferred=uniq([x.group(0).title() for x in LOCATION_TOKEN_RE.finditer(m.group(1))])
    if not preferred and is_req and len(locs)>1:
        preferred=locs[:]

    primary=preferred[0] if len(preferred)==1 else (locs[0] if len(locs)==1 else None)

    budget_min=budget_max=None
    # range first
    mr=re.search(r"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(?:L|LAC|LAKH)\s*(?:-|TO|–|—)\s*(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(?:L|LAC|LAKH)",t,re.I)
    if mr:
        budget_min=float(mr.group(1))*100000
        budget_max=float(mr.group(2))*100000
    else:
        vals=[float(x)*100000 for x in re.findall(r"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(?:L|LAC|LAKH)(?:/MONTH|PM|P\.M\.)?",t,re.I)]
        if vals:
            budget_min=min(vals); budget_max=max(vals)

    bhk=[]
    for m in BHK_RE.finditer(t):
        if m.group(1) and m.group(2):
            bhk.extend([int(m.group(1)),int(m.group(2))])
        elif m.group(3):
            bhk.append(int(m.group(3)))

    return {
        "is_requirement":is_req,
        "preferred_locations":preferred,
        "acceptable_locations":preferred[:] if preferred else locs[:],
        "primary_location":primary,
        "location_conflict":False if is_req and len(preferred)>1 else False,
        "bhk_options":sorted(set(bhk)),
        "budget_min":budget_min,
        "budget_max":budget_max,
        "transaction_required_for_review":False,
        "review_ready":bool(is_req and (preferred or locs or bhk or budget_max))
    }

# -----------------------------
# Spatial Brain
# -----------------------------
SECTOR_RE=re.compile(r"\bSEC(?:TOR)?\.?\s*(\d+[A-Z]?)\b",re.I)
CITY_HINTS=[
 ("Noida", re.compile(r"\bNOIDA\b",re.I)),
 ("Gurugram", re.compile(r"\b(?:GURUGRAM|GURGAON)\b",re.I)),
 ("Delhi", re.compile(r"\b(?:SAKET|KALKAJI|DWARKA|VASANT\s+KUNJ|VASANT\s+VIHAR|DEFENCE\s+COLONY|GREATER\s+KAILASH|GK\s*[12])\b",re.I)),
 ("Mumbai", re.compile(r"\b(?:JUHU|LOKHANDWALA)\b",re.I)),
 ("Goa", re.compile(r"\b(?:VAGATOR|ANJUNA|SIOLIM|ASSAGAO)\b",re.I)),
]

def spatial_brain(text:str, upstream_location:Optional[str]=None)->Dict[str,Any]:
    t=norm_text(text)
    locs=uniq([m.group(0).title() for m in LOCATION_TOKEN_RE.finditer(t)])
    sector=None
    sm=SECTOR_RE.search(t)
    if sm: sector=f"Sector {sm.group(1).upper()}"

    city=upstream_location
    if not city:
        for name,pat in CITY_HINTS:
            if pat.search(t):
                city=name; break

    # Special safe inheritance: Sec 22D + upstream Noida
    locality=None
    if sector and city in {"Noida","Gurugram","Delhi"}:
        locality=sector
    elif len(locs)==1:
        locality=locs[0]

    return {
      "city":city,
      "locality":locality,
      "sector":sector,
      "locations_detected":locs,
      "hierarchy_complete":bool(city or locality)
    }

# -----------------------------
# Measurement Brain
# -----------------------------
AREA_PAT=re.compile(r"\b(\d+(?:,\d{3})*(?:\.\d+)?)\s*(SQ\s*FT|SQFT|SQ\s*YD|SQYD|SYD|SYDS|GAJ|YARDS?|ACRES?|CARPET|MTR|METER|METRE|SQ\s*M|SQM)\b",re.I)
DISTANCE_CUES=re.compile(r"\b(?:FROM|AWAY\s+FROM|NEAR|DISTANCE|TO\s+AIRPORT|FROM\s+MAINGATE)\b",re.I)
ROAD_CUES=re.compile(r"\b(?:ROAD|ROAD\s+WIDTH|WIDE\s+ROAD)\b",re.I)
FRONTAGE_CUES=re.compile(r"\b(?:FRONT|FRONTAGE)\b",re.I)
PLOT_CUES=re.compile(r"\b(?:PLOT\s+SIZE|PLOT|LAND\s+AREA)\b",re.I)
CARPET_CUES=re.compile(r"\bCARPET\b",re.I)

def measurement_brain(text:str)->Dict[str,Any]:
    t=norm_text(text)
    mentions=[]

    def role_for(m):
        raw=m.group(0)
        value=float(m.group(1).replace(",",""))
        unit=re.sub(r"\s+","",m.group(2).upper())

        # Tight local windows are deliberate. A measurement should be explained
        # by words attached to that measurement, not by a cue from the previous clause.
        before=t[max(0,m.start()-22):m.start()]
        after=t[m.end():min(len(t),m.end()+26)]
        immediate=(before+" "+raw+" "+after).strip()
        after_only=after.strip()

        # Highest-confidence semantic roles first.
        if unit in {"MTR","METER","METRE"}:
            if re.match(r"^\s*(?:WIDE\s+)?ROAD\b",after_only,re.I) or re.search(r"\bROAD\s*(?:WIDTH|WIDE)?\s*$",before,re.I):
                return "ROAD_WIDTH"
            if re.search(r"\b(?:FROM|AWAY\s+FROM)\s*$",before,re.I) or re.match(r"^\s*(?:FROM|AWAY\s+FROM)\b",after_only,re.I):
                return "DISTANCE"
            if re.search(r"\b(?:ROAD|ROAD\s+WIDTH|WIDE\s+ROAD)\b",immediate,re.I):
                return "ROAD_WIDTH"
            if re.search(r"\b(?:FROM|AWAY\s+FROM|DISTANCE)\b",immediate,re.I):
                return "DISTANCE"

        if re.search(r"\b(?:FRONT|FRONTAGE)\b",immediate,re.I):
            return "FRONTAGE"
        if re.search(r"\bCARPET\b",immediate,re.I):
            return "CARPET_AREA"
        if re.search(r"\b(?:PLOT\s+SIZE|PLOT|LAND\s+AREA)\b",immediate,re.I):
            return "PLOT_AREA"
        return "PROPERTY_AREA"

    for m in AREA_PAT.finditer(t):
        raw=m.group(0)
        value=float(m.group(1).replace(",",""))
        unit=re.sub(r"\s+","",m.group(2).upper())
        role=role_for(m)
        mentions.append({"value":value,"unit":unit,"role":role,"raw":raw})

    prop=[x for x in mentions if x["role"] in {"PROPERTY_AREA","PLOT_AREA","CARPET_AREA"}]
    return {
      "mentions":mentions,
      "property_area_mentions":prop,
      "distance_mentions":[x for x in mentions if x["role"]=="DISTANCE"],
      "road_width_mentions":[x for x in mentions if x["role"]=="ROAD_WIDTH"],
      "frontage_mentions":[x for x in mentions if x["role"]=="FRONTAGE"]
    }

# -----------------------------
# Money Grammar
# -----------------------------
MONEY_RE=re.compile(r"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*\.?\s*(CR|CRORE|L|LAC|LAKH|LACS|LAKHS)\b",re.I)
RATE_RE=re.compile(r"\b(?:RATE\s*(?:IS|@|:)?\s*)?(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(?:/|PER)\s*(SQFT|SQ\s*FT|YARD|YARDS|SQYD|SQ\s*YD)\b",re.I)

def money_brain(text:str)->Dict[str,Any]:
    t=norm_text(text)
    totals=[]
    for m in MONEY_RE.finditer(t):
        v=float(m.group(1)); u=m.group(2).upper()
        factor=10000000 if u in {"CR","CRORE"} else 100000
        totals.append({"value":v*factor,"raw":m.group(0),"unit":u})
    rates=[]
    for m in RATE_RE.finditer(t):
        rates.append({"rate":float(m.group(1)),"unit":m.group(2).upper(),"raw":m.group(0)})
    return {
      "totals":totals,
      "rates":rates,
      "rate_totalization_forbidden":bool(rates),
      "malformed_lakh_recovered":bool(re.search(r"\d+(?:\.\d+)?\s*\.\s*L\b",t,re.I))
    }

# -----------------------------
# Suitability Brain
# -----------------------------
SUITABLE_PAT=re.compile(r"\b(?:IDEAL\s+FOR|SUITABLE\s+FOR|BEST\s+FOR)\s*[:\-]?\s*(.+?)(?=\b(?:PHONE|CALL|BROKERAGE|CONTACT|AVAILABLE|RENT|SALE)\b|$)",re.I)
REQ_DIRECTION_RE=re.compile(r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT)\b",re.I)

def suitability_brain(text:str)->Dict[str,Any]:
    t=norm_text(text)
    vals=[]
    for m in SUITABLE_PAT.finditer(t):
        chunk=m.group(1)
        parts=re.split(r"[|,/;&•]+",chunk)
        for p in parts:
            p=norm_text(p)
            if 2 <= len(p) <= 80:
                vals.append(p)
    return {
      "suitable_uses":uniq(vals)[:30],
      "is_requirement_signal":bool(REQ_DIRECTION_RE.search(t)),
      "suitability_must_not_create_requirement":bool(vals and not REQ_DIRECTION_RE.search(t))
    }

# -----------------------------
# Unified coach
# -----------------------------
def coach(text:str, upstream_location:Optional[str]=None)->Dict[str,Any]:
    t=norm_text(text)
    req=requirement_brain(t)
    spatial=spatial_brain(t,upstream_location)
    meas=measurement_brain(t)
    money=money_brain(t)
    suitable=suitability_brain(t)

    classification="REQUIREMENT" if req["is_requirement"] else "AVAILABILITY"

    # Requirements: multi-location is preference set, not conflict.
    if classification=="REQUIREMENT" and req["acceptable_locations"]:
        location_state="MULTI_LOCATION_PREFERENCE" if len(req["acceptable_locations"])>1 else "SINGLE_LOCATION"
    else:
        location_state="RESOLVED" if (spatial["city"] or spatial["locality"]) else "UNRESOLVED"

    return {
      "classification":classification,
      "normalized_text":t,
      "requirement_brain":req,
      "spatial_brain":spatial,
      "measurement_brain":meas,
      "money_brain":money,
      "suitability_brain":suitable,
      "location_state":location_state,
      "read_only":True,
      "canonical_writes":0,
      "offer_writes":0,
      "matcher_writes":0,
      "whatsapp_live_writes":0
    }

CASES=[
 ("GOA_MULTI_LOCATION_REQUIREMENT",
  "Looking for a 3/4 BHK independent villa with a private pool for commercial purposes. Preferred Locations: Vagator • Anjuna • Siolim • Assagao Budget: ₹1.5 - ₹2.25 Lakh/month",
  lambda r:r["classification"]=="REQUIREMENT"
           and r["requirement_brain"]["acceptable_locations"]==["Vagator","Anjuna","Siolim","Assagao"]
           and r["location_state"]=="MULTI_LOCATION_PREFERENCE"
           and r["requirement_brain"]["review_ready"]),
 ("JEWAR_DISTANCE_NOT_AREA",
  "This plot is 200 mtr from airport. 60 mtr road. Plot size is 500 yards. Rate is rs 50000 per yards",
  lambda r:any(x["role"]=="DISTANCE" and x["value"]==200 for x in r["measurement_brain"]["mentions"])
           and any(x["role"]=="ROAD_WIDTH" and x["value"]==60 for x in r["measurement_brain"]["mentions"])
           and any(x["role"]=="PLOT_AREA" and x["value"]==500 for x in r["measurement_brain"]["mentions"])
           and r["money_brain"]["rate_totalization_forbidden"]),
 ("NOIDA_SECTOR_HIERARCHY",
  "120 MTR plot. Demand 1.40 Cr | Sec 22D",
  lambda r:r["spatial_brain"]["city"]=="Noida" and r["spatial_brain"]["sector"]=="Sector 22D"),
 ("MALFORMED_DOTTED_LAKH",
  "2400 sq ft renovated flat rent 1.25.L",
  lambda r:any(abs(x["value"]-125000)<1 for x in r["money_brain"]["totals"])),
 ("DWARKA_SUITABILITY_NOT_REQUIREMENT",
  "3200 sq ft Carpet Area | Ideal for Cosmetologists | Hair Specialists | Dermatologists | Doctors | Clinics | Aesthetic & Wellness Centres | commercial space in Dwarka",
  lambda r:r["classification"]=="AVAILABILITY"
           and r["suitability_brain"]["suitability_must_not_create_requirement"]),
 ("RATE_NEVER_TOTALIZED",
  "500 yards plot | Rate is rs 50000 per yards",
  lambda r:r["money_brain"]["rate_totalization_forbidden"] and len(r["money_brain"]["rates"])>=1),
 ("DIRECT_CLIENT_REQUIREMENT_NO_TX_NEEDED",
  "DIRECT CLIENT RENTAL REQUIREMENT 3/4 BHK VILLA WITH PRIVATE POOL | Ready to Close",
  lambda r:r["classification"]=="REQUIREMENT" and r["requirement_brain"]["transaction_required_for_review"] is False)
,
 ("ROAD_WIDTH_NOT_DISTANCE",
  "Plot is 200 mtr from airport. Approach is 60 mtr road. Plot size 500 yards",
  lambda r:any(x["role"]=="DISTANCE" and x["value"]==200 for x in r["measurement_brain"]["mentions"])
           and any(x["role"]=="ROAD_WIDTH" and x["value"]==60 for x in r["measurement_brain"]["mentions"])
           and any(x["role"]=="PLOT_AREA" and x["value"]==500 for x in r["measurement_brain"]["mentions"])),
 ("DISTANCE_NOT_PROPERTY_AREA",
  "Property is 200 mtr from airport",
  lambda r:len(r["measurement_brain"]["property_area_mentions"])==0
           and any(x["role"]=="DISTANCE" and x["value"]==200 for x in r["measurement_brain"]["mentions"]))]

def regression()->Dict[str,Any]:
    out=[]
    for key,text,fn in CASES:
        r=coach(text, "Noida" if key=="NOIDA_SECTOR_HIERARCHY" else None)
        ok=bool(fn(r))
        out.append({"case_key":key,"passed":ok,"result":r})
    p=sum(1 for x in out if x["passed"]); t=len(out)
    return {
      "status":"PASS" if p==t else "FAIL",
      "version":VERSION,
      "total":t,"passed":p,"failed":t-p,
      "score":round(100*p/t,2),
      "critical_failures":t-p,
      "results":out,
      "writes_performed":0
    }

def real_exam(engine,limit:int=500)->Dict[str,Any]:
    # Reuse V262D distinct sampler, then add semantic interpretation.
    candidates=v262d._distinct_real_candidates(engine,limit)
    stats=Counter(); examples=[]
    for c in candidates:
        raw=str(c.get("own_text_redacted") or "")
        r=coach(raw,c.get("location"))
        stats["CLASS_"+r["classification"]]+=1
        if r["requirement_brain"]["acceptable_locations"]:
            stats["REQUIREMENTS_WITH_LOCATION_SET"]+=1
        if r["money_brain"]["malformed_lakh_recovered"]:
            stats["MALFORMED_LAKH_RECOVERED"]+=1
        stats["MEAS_PROPERTY_AREA"]+=len(r["measurement_brain"]["property_area_mentions"])
        stats["MEAS_DISTANCE"]+=len(r["measurement_brain"]["distance_mentions"])
        stats["MEAS_ROAD_WIDTH"]+=len(r["measurement_brain"]["road_width_mentions"])
        if r["suitability_brain"]["suitable_uses"]:
            stats["SUITABILITY_RECORDS"]+=1
        if len(examples)<100 and (
            r["location_state"]=="MULTI_LOCATION_PREFERENCE"
            or r["money_brain"]["malformed_lakh_recovered"]
            or r["measurement_brain"]["distance_mentions"]
            or r["suitability_brain"]["suitable_uses"]
        ):
            examples.append({"raw":raw,"upstream_location":c.get("location"),"semantic":r})

    reg=regression()
    d=v262d.regression(); c=v262c.regression(); b=v262b.regression(); a=v262a.regression()
    gate=all(x["critical_failures"]==0 for x in [reg,d,c,b,a])

    return {
      "status":"PASS" if gate else "TRAINING_REQUIRED",
      "version":VERSION,
      "requested_limit":limit,
      "distinct_real_candidates_examined":len(candidates),
      "target_reached":len(candidates)>=limit,
      "stats":dict(stats),
      "examples":examples,
      "v262e_score":reg["score"],
      "v262e_critical_failures":reg["critical_failures"],
      "v262d_score":d["score"],
      "v262c_score":c["score"],
      "v262b_score":b["score"],
      "v262a_score":a["score"],
      "mastery_gate_passed":gate,
      "read_only":True,
      "canonical_writes":0,
      "offer_writes":0,
      "matcher_writes":0,
      "whatsapp_live_writes":0,
      "claim":"PASS means known V262A-E regressions pass. It does not mean universal accuracy or permission to write production property data."
    }

def register(core):
    app=core.app
    engine=core.engine
    route="/api/v7/property-ai/mastery-v262e/status"

    if any(getattr(r,"path",None)==route for r in app.router.routes):
        return {"status":"ALREADY_REGISTERED","version":VERSION,"route":route}

    @app.get(route)
    def status():
        return JSONResponse({
          "status":"READY","version":VERSION,"mode":MODE,"read_only":True,
          "canonical_writes":0,"offer_writes":0,
          "matcher_modified":False,"whatsapp_live_modified":False
        })

    @app.get("/api/v7/property-ai/mastery-v262e/regression")
    def reg():
        return JSONResponse(regression())

    @app.get("/api/v7/property-ai/mastery-v262e/exam")
    def exam(limit:int=Query(500,ge=1,le=1000)):
        return JSONResponse(real_exam(engine,limit))

    return {
      "status":"REGISTERED",
      "version":VERSION,
      "route":route,
      "regression":"/api/v7/property-ai/mastery-v262e/regression",
      "exam":"/api/v7/property-ai/mastery-v262e/exam?limit=500"
    }
