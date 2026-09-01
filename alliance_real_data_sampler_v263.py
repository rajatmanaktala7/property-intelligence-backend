from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

import alliance_semantic_context_mastery_v262e as v262e
import alliance_context_recovery_academy_v262d as v262d
import alliance_excellence_mastery_v262c as v262c
import alliance_boundary_context_mastery_v262b as v262b
import alliance_real_mastery_v262a as v262a

VERSION="2.6.3-REAL-DATA-SAMPLER-SEMANTIC-PRECISION"
MODE="READ_ONLY_REAL_MASTER_SAMPLER_PLUS_SEMANTIC_PRECISION"

# ---------------------------------------------------------------------
# Precision layer
# ---------------------------------------------------------------------

LOCALITY_TO_CITY = {
    "Vagator":"Goa","Anjuna":"Goa","Siolim":"Goa","Assagao":"Goa",
    "Saket":"Delhi","Kalkaji":"Delhi","Dwarka":"Delhi","Vasant Kunj":"Delhi",
    "Vasant Vihar":"Delhi","Defence Colony":"Delhi","Greater Kailash 1":"Delhi",
    "Greater Kailash 2":"Delhi","Juhu":"Mumbai","Lokhandwala":"Mumbai",
    "Lokhandwala Back Road":"Mumbai"
}

CITY_NAMES={"Delhi","Mumbai","Noida","Gurugram","Goa"}

STRONG_REQ_START_RE=re.compile(
    r"^\s*(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|"
    r"BUYER\s+REQUIREMENT|RENTAL\s+REQUIREMENT|PURCHASE\s+REQUIREMENT|"
    r"CLIENT\s+REQUIREMENT|REQUIREMENT\s*[:\-])\b",re.I
)
STRONG_REQ_ANY_RE=re.compile(
    r"\b(?:DIRECT\s+CLIENT|BUYER\s+REQUIREMENT|RENTAL\s+REQUIREMENT|"
    r"PURCHASE\s+REQUIREMENT|CLIENT\s+REQUIREMENT)\b",re.I
)
MARKETING_LOOKING_RE=re.compile(
    r"\b(?:BRANDS?|PROFESSIONALS?|CLIENTS?|EXECUTIVES?|FAMILIES|COMPANIES)\s+LOOKING\s+FOR\b",re.I
)

LOC_RE=v262e.LOCATION_TOKEN_RE

MONEY_UNIT_RE=r"(?:L|LAC|LAKH|LACS|LAKHS)"
BUDGET_SHARED_UNIT_RANGE_RE=re.compile(
    rf"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(?:-|TO|–|—|â+)\s*"
    rf"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*{MONEY_UNIT_RE}(?:/MONTH|/MO|PM|P\.M\.)?",
    re.I
)
BUDGET_FULL_RANGE_RE=re.compile(
    rf"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*{MONEY_UNIT_RE}\s*"
    rf"(?:-|TO|–|—|â+)\s*(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*{MONEY_UNIT_RE}",
    re.I
)

MEAS_RE=re.compile(
    r"\b(\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(SQ\s*FT|SQFT|SQ\s*YD|SQYD|SYD|SYDS|GAJ|YARDS?|ACRES?|CARPET|"
    r"MTR|METER|METRE|SQ\s*M|SQM|FEET|FT)\b",re.I
)

def _norm(s:Any)->str:
    return v262e.norm_text(s)

def _uniq(xs):
    out=[]
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out

def requirement_brain(text_value:str)->Dict[str,Any]:
    t=_norm(text_value)
    is_req=bool(STRONG_REQ_START_RE.search(t) or STRONG_REQ_ANY_RE.search(t))
    if MARKETING_LOOKING_RE.search(t) and not STRONG_REQ_START_RE.search(t) and not STRONG_REQ_ANY_RE.search(t):
        is_req=False

    locs=_uniq([m.group(0).title() for m in LOC_RE.finditer(t)])

    preferred=[]
    pm=re.search(
        r"\b(?:PREFERRED\s+LOCATIONS?|ACCEPTABLE\s+LOCATIONS?)\s*[:\-]?\s*(.+?)"
        r"(?=\b(?:BUDGET|POSSESSION|READY\s+TO\s+CLOSE|KINDLY|CONTACT|PHONE)\b|$)",
        t,re.I
    )
    if pm:
        preferred=_uniq([m.group(0).title() for m in LOC_RE.finditer(pm.group(1))])
    elif is_req and len(locs)>1:
        preferred=locs[:]

    budget_min=budget_max=None
    bm=BUDGET_FULL_RANGE_RE.search(t) or BUDGET_SHARED_UNIT_RANGE_RE.search(t)
    if bm:
        budget_min=float(bm.group(1))*100000
        budget_max=float(bm.group(2))*100000
        if budget_min>budget_max:
            budget_min,budget_max=budget_max,budget_min
    else:
        vals=[
            float(x)*100000
            for x in re.findall(
                rf"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*{MONEY_UNIT_RE}(?:/MONTH|/MO|PM|P\.M\.)?",
                t,re.I
            )
        ]
        if vals:
            budget_min=min(vals); budget_max=max(vals)

    bhk=[]
    for m in re.finditer(r"\b(\d+)\s*/\s*(\d+)\s*BHK\b|\b(\d+)\s*BHK\b",t,re.I):
        if m.group(1) and m.group(2):
            bhk.extend([int(m.group(1)),int(m.group(2))])
        elif m.group(3):
            bhk.append(int(m.group(3)))

    acceptable=preferred[:] if preferred else (locs[:] if is_req else [])
    primary=acceptable[0] if len(acceptable)==1 else None

    return {
        "is_requirement":is_req,
        "preferred_locations":preferred,
        "acceptable_locations":acceptable,
        "primary_location":primary,
        "location_conflict":False,
        "bhk_options":sorted(set(bhk)),
        "budget_min":budget_min,
        "budget_max":budget_max,
        "transaction_required_for_review":False,
        "review_ready":bool(is_req and (acceptable or bhk or budget_max))
    }

def spatial_brain(text_value:str, upstream_location:Optional[str]=None)->Dict[str,Any]:
    t=_norm(text_value)
    detected=_uniq([m.group(0).title() for m in LOC_RE.finditer(t)])

    sector=None
    sm=re.search(r"\bSEC(?:TOR)?\.?\s*(\d+[A-Z]?)\b",t,re.I)
    if sm:
        sector=f"Sector {sm.group(1).upper()}"

    upstream=(upstream_location or "").strip() or None
    city=None
    locality=None

    if upstream in CITY_NAMES:
        city=upstream
    elif upstream in LOCALITY_TO_CITY:
        locality=upstream
        city=LOCALITY_TO_CITY[upstream]

    if not city:
        for loc in detected:
            if loc in LOCALITY_TO_CITY:
                city=LOCALITY_TO_CITY[loc]
                if len(detected)==1:
                    locality=loc
                break
            if loc in {"Noida","Gurugram"}:
                city=loc
                if len(detected)==1:
                    locality=loc
                break

    if sector and city:
        locality=sector
    elif not locality and len(detected)==1:
        locality=detected[0]

    return {
        "city":city,
        "locality":locality,
        "sector":sector,
        "upstream_location":upstream,
        "locations_detected":detected,
        "hierarchy_complete":bool(city or locality)
    }

def measurement_brain(text_value:str)->Dict[str,Any]:
    t=_norm(text_value)
    mentions=[]

    def role_for(m):
        raw=m.group(0)
        unit=re.sub(r"\s+","",m.group(2).upper())
        before=t[max(0,m.start()-30):m.start()]
        after=t[m.end():min(len(t),m.end()+30)]
        before_tight=t[max(0,m.start()-18):m.start()]
        after_tight=t[m.end():min(len(t),m.end()+18)]

        # Cue attached BEFORE a number has priority over words after the number.
        # "Plot size is 500 yards front is 85 feet" => 500 is PLOT_AREA.
        if re.search(r"\b(?:PLOT\s+SIZE(?:\s+IS)?|PLOT|LAND\s+AREA)\s*(?:IS|:|-)?\s*$",before_tight,re.I):
            return "PLOT_AREA"
        if re.search(r"\b(?:CARPET(?:\s+AREA)?|CARPET\s+SIZE)\s*(?:IS|:|-)?\s*$",before_tight,re.I):
            return "CARPET_AREA"
        if re.search(r"\b(?:FRONT|FRONTAGE)\s*(?:IS|:|-)?\s*$",before_tight,re.I):
            return "FRONTAGE"
        if re.search(r"\b(?:ROAD\s+WIDTH|ROAD)\s*(?:IS|:|-)?\s*$",before_tight,re.I):
            return "ROAD_WIDTH"

        if unit in {"MTR","METER","METRE"}:
            if re.match(r"^\s*(?:WIDE\s+)?ROAD\b",after_tight,re.I):
                return "ROAD_WIDTH"
            if re.match(r"^\s*(?:FROM|AWAY\s+FROM)\b",after_tight,re.I):
                return "DISTANCE"
            if re.search(r"\b(?:FROM|AWAY\s+FROM)\s*$",before_tight,re.I):
                return "DISTANCE"

        if unit in {"FEET","FT"} and re.search(r"\b(?:FRONT|FRONTAGE)\b",before,re.I):
            return "FRONTAGE"
        if re.search(r"\bCARPET\b",before_tight+" "+raw,re.I):
            return "CARPET_AREA"

        # Do not let a later "front is ..." re-label a preceding plot area.
        return "PROPERTY_AREA"

    for m in MEAS_RE.finditer(t):
        raw=m.group(0)
        value=float(m.group(1).replace(",",""))
        unit=re.sub(r"\s+","",m.group(2).upper())
        role=role_for(m)
        mentions.append({"value":value,"unit":unit,"role":role,"raw":raw})

    return {
        "mentions":mentions,
        "property_area_mentions":[x for x in mentions if x["role"] in {"PROPERTY_AREA","PLOT_AREA","CARPET_AREA"}],
        "distance_mentions":[x for x in mentions if x["role"]=="DISTANCE"],
        "road_width_mentions":[x for x in mentions if x["role"]=="ROAD_WIDTH"],
        "frontage_mentions":[x for x in mentions if x["role"]=="FRONTAGE"]
    }

def suitability_brain(text_value:str)->Dict[str,Any]:
    t=_norm(text_value)
    vals=[]

    # Only capture explicit suitability clauses, one clause at a time.
    for part in re.split(r"\s*\|\s*",t):
        m=re.search(r"\b(?:IDEAL\s+FOR|SUITABLE\s+FOR|BEST\s+FOR)\s*[:\-]?\s*(.+)$",part,re.I)
        if not m:
            continue
        chunk=m.group(1).strip()
        for item in re.split(r"[,/&;•]+",chunk):
            item=_norm(item)
            if 2<=len(item)<=60 and not re.search(r"\b(?:PHONE|CALL|BUDGET|BROKERAGE|CONTACT)\b",item,re.I):
                vals.append(item)

    req=requirement_brain(t)["is_requirement"]
    return {
        "suitable_uses":_uniq(vals)[:20],
        "is_requirement_signal":req,
        "suitability_must_not_create_requirement":bool(vals and not req)
    }

def coach(text_value:str, upstream_location:Optional[str]=None)->Dict[str,Any]:
    t=_norm(text_value)
    req=requirement_brain(t)
    spatial=spatial_brain(t,upstream_location)
    meas=measurement_brain(t)
    money=v262e.money_brain(t)
    suitable=suitability_brain(t)

    classification="REQUIREMENT" if req["is_requirement"] else "AVAILABILITY"
    if classification=="REQUIREMENT" and len(req["acceptable_locations"])>1:
        location_state="MULTI_LOCATION_PREFERENCE"
    elif spatial["city"] or spatial["locality"]:
        location_state="RESOLVED"
    else:
        location_state="UNRESOLVED"

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

# ---------------------------------------------------------------------
# Direct real-data sampler
# ---------------------------------------------------------------------

PREFERRED_TEXT_COLUMNS=[
    "raw_text","message_text","message","text","content","description",
    "property_text","raw_message","body","source_text","clean_text"
]
PREFERRED_LOCATION_COLUMNS=[
    "location","locality","city","area","property_location"
]

def _quote_ident(name:str)->str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",name or ""):
        raise ValueError("unsafe identifier")
    return '"' + name.replace('"','""') + '"'

def _table_columns(engine, table_name:str)->List[Dict[str,str]]:
    q=text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema=current_schema()
          AND table_name=:table_name
        ORDER BY ordinal_position
    """)
    with engine.connect() as conn:
        rows=conn.execute(q,{"table_name":table_name}).mappings().all()
    return [{"column_name":r["column_name"],"data_type":r["data_type"]} for r in rows]

def _choose_column(columns:List[Dict[str,str]], preferred:List[str])->Optional[str]:
    names=[c["column_name"] for c in columns]
    lower={n.lower():n for n in names}
    for p in preferred:
        if p.lower() in lower:
            return lower[p.lower()]

    # Fallback: textual columns with meaningful names.
    text_types={"text","character varying","character","varchar"}
    for c in columns:
        if c["data_type"].lower() in text_types:
            n=c["column_name"]
            if re.search(r"(text|message|content|description|body|property|raw)",n,re.I):
                return n
    return None

def master_sampler(engine, limit:int=500)->Dict[str,Any]:
    table="pi_whatsapp_property_master"
    cols=_table_columns(engine,table)
    text_col=_choose_column(cols,PREFERRED_TEXT_COLUMNS)
    location_col=_choose_column(cols,PREFERRED_LOCATION_COLUMNS)

    if not cols:
        return {
            "status":"SOURCE_TABLE_NOT_FOUND","table":table,"rows":[],"sampled":0,
            "target_reached":False,"writes":0
        }
    if not text_col:
        return {
            "status":"TEXT_COLUMN_NOT_FOUND","table":table,
            "available_columns":[c["column_name"] for c in cols],
            "rows":[],"sampled":0,"target_reached":False,"writes":0
        }

    qt=_quote_ident(table)
    qc=_quote_ident(text_col)
    ql=_quote_ident(location_col) if location_col else None

    # Hash ordering provides deterministic diversity over the full master table
    # without modifying the source or depending on a primary key.
    loc_select=f", {ql}::text AS upstream_location" if ql else ", NULL::text AS upstream_location"
    sql=text(
        f"""SELECT {qc}::text AS raw_text {loc_select}
            FROM {qt}
            WHERE {qc} IS NOT NULL
              AND length(trim({qc}::text)) >= 20
            ORDER BY md5({qc}::text)
            LIMIT :fetch_limit"""
    )

    fetch_limit=min(max(limit*3,limit),5000)
    with engine.connect() as conn:
        raw_rows=conn.execute(sql,{"fetch_limit":fetch_limit}).mappings().all()

    seen=set()
    rows=[]
    for r in raw_rows:
        raw=_norm(r["raw_text"])
        if not raw:
            continue
        key=re.sub(r"\s+"," ",raw).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"raw":raw,"upstream_location":r.get("upstream_location")})
        if len(rows)>=limit:
            break

    return {
        "status":"PASS",
        "table":table,
        "text_column":text_col,
        "location_column":location_col,
        "rows":rows,
        "sampled":len(rows),
        "target_reached":len(rows)>=limit,
        "writes":0
    }

def real_exam(engine,limit:int=500)->Dict[str,Any]:
    sample=master_sampler(engine,limit)
    if sample["status"]!="PASS":
        return {
            "status":"SAMPLER_NOT_READY","version":VERSION,
            "sampler":sample,"read_only":True,
            "canonical_writes":0,"offer_writes":0,"matcher_writes":0,"whatsapp_live_writes":0
        }

    stats=Counter()
    examples=[]
    for row in sample["rows"]:
        r=coach(row["raw"],row.get("upstream_location"))
        stats["CLASS_"+r["classification"]]+=1
        stats["MEAS_PROPERTY_AREA"]+=len(r["measurement_brain"]["property_area_mentions"])
        stats["MEAS_DISTANCE"]+=len(r["measurement_brain"]["distance_mentions"])
        stats["MEAS_ROAD_WIDTH"]+=len(r["measurement_brain"]["road_width_mentions"])
        stats["MEAS_FRONTAGE"]+=len(r["measurement_brain"]["frontage_mentions"])
        if r["requirement_brain"]["acceptable_locations"]:
            stats["REQUIREMENT_LOCATION_SETS"]+=1
        if r["money_brain"]["malformed_lakh_recovered"]:
            stats["MALFORMED_LAKH_RECOVERED"]+=1
        if r["suitability_brain"]["suitable_uses"]:
            stats["SUITABILITY_RECORDS"]+=1

        if len(examples)<100 and (
            r["classification"]=="REQUIREMENT"
            or r["measurement_brain"]["distance_mentions"]
            or r["measurement_brain"]["road_width_mentions"]
            or r["measurement_brain"]["frontage_mentions"]
            or r["suitability_brain"]["suitable_uses"]
            or r["money_brain"]["malformed_lakh_recovered"]
        ):
            examples.append({"raw":row["raw"],"upstream_location":row.get("upstream_location"),"semantic":r})

    reg=regression()
    e=v262e.regression()
    d=v262d.regression()
    c=v262c.regression()
    b=v262b.regression()
    a=v262a.regression()

    gate=all(x["critical_failures"]==0 for x in [reg,e,d,c,b,a])

    return {
        "status":"PASS" if gate else "TRAINING_REQUIRED",
        "version":VERSION,
        "requested_limit":limit,
        "distinct_real_candidates_examined":sample["sampled"],
        "target_reached":sample["target_reached"],
        "source_table":sample["table"],
        "source_text_column":sample["text_column"],
        "source_location_column":sample["location_column"],
        "stats":dict(stats),
        "examples":examples,
        "v263_score":reg["score"],
        "v263_critical_failures":reg["critical_failures"],
        "v262e_score":e["score"],
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
        "claim":"V263 directly samples distinct rows from pi_whatsapp_property_master. PASS is a regression/sampler safety result, not universal semantic accuracy or write permission."
    }

CASES=[
    (
        "PLOT_AREA_BEATS_LATER_FRONTAGE_CUE",
        "Plot size is 500 yards front is 85 feet",
        lambda r:
            any(x["role"]=="PLOT_AREA" and x["value"]==500 for x in r["measurement_brain"]["mentions"])
            and any(x["role"]=="FRONTAGE" and x["value"]==85 for x in r["measurement_brain"]["mentions"])
    ),
    (
        "DISTANCE_ROAD_PLOT_SEPARATED",
        "It is 200 mtr from airport. Approach is 60 mtr road. Plot size is 500 yards",
        lambda r:
            any(x["role"]=="DISTANCE" and x["value"]==200 for x in r["measurement_brain"]["mentions"])
            and any(x["role"]=="ROAD_WIDTH" and x["value"]==60 for x in r["measurement_brain"]["mentions"])
            and any(x["role"]=="PLOT_AREA" and x["value"]==500 for x in r["measurement_brain"]["mentions"])
    ),
    (
        "SHARED_UNIT_BUDGET_RANGE",
        "Looking for villa. Preferred Locations: Vagator Anjuna Siolim Assagao | Budget: ₹1.5 - ₹2.25 Lakh/month",
        lambda r:
            r["classification"]=="REQUIREMENT"
            and r["requirement_brain"]["budget_min"]==150000.0
            and r["requirement_brain"]["budget_max"]==225000.0
    ),
    (
        "MARKETING_LOOKING_FOR_NOT_REQUIREMENT",
        "Ideal for established professionals and premium brands looking for a spacious commercial space in Dwarka",
        lambda r:r["classification"]=="AVAILABILITY"
    ),
    (
        "LOCALITY_UPSTREAM_IS_NOT_CITY",
        "Looking for villa in Vagator",
        lambda r:
            coach("Looking for villa in Vagator","Vagator")["spatial_brain"]["city"]=="Goa"
            and coach("Looking for villa in Vagator","Vagator")["spatial_brain"]["locality"]=="Vagator"
    ),
    (
        "SUITABILITY_STAYS_CLEAN",
        "3200 sq ft | Ideal for Cosmetologists | Hair Specialists | Doctors | Clinics | commercial space in Dwarka",
        lambda r:
            r["classification"]=="AVAILABILITY"
            and "Cosmetologists" in r["suitability_brain"]["suitable_uses"]
    ),
    (
        "MALFORMED_LAKH_PRESERVED",
        "2400 sq ft rent 1.25.L",
        lambda r:any(abs(x["value"]-125000.0)<1 for x in r["money_brain"]["totals"])
    )
]

def regression()->Dict[str,Any]:
    out=[]
    for key,raw,fn in CASES:
        r=coach(raw)
        ok=bool(fn(r))
        out.append({"case_key":key,"passed":ok,"result":r})
    passed=sum(1 for x in out if x["passed"])
    total=len(out)
    return {
        "status":"PASS" if passed==total else "FAIL",
        "version":VERSION,
        "total":total,
        "passed":passed,
        "failed":total-passed,
        "score":round(100.0*passed/total,2),
        "critical_failures":total-passed,
        "results":out,
        "writes_performed":0
    }

def register(core):
    app=core.app
    engine=core.engine
    route="/api/v7/property-ai/mastery-v263/status"

    if any(getattr(r,"path",None)==route for r in app.router.routes):
        return {"status":"ALREADY_REGISTERED","version":VERSION,"route":route}

    @app.get(route)
    def status():
        return JSONResponse({
            "status":"READY","version":VERSION,"mode":MODE,"read_only":True,
            "canonical_writes":0,"offer_writes":0,
            "matcher_modified":False,"whatsapp_live_modified":False
        })

    @app.get("/api/v7/property-ai/mastery-v263/regression")
    def reg():
        return JSONResponse(regression())

    @app.get("/api/v7/property-ai/mastery-v263/sample")
    def sample(limit:int=Query(500,ge=1,le=1000)):
        s=master_sampler(engine,limit)
        # return only first 25 examples to keep response manageable
        return JSONResponse({**s,"rows":s.get("rows",[])[:25]})

    @app.get("/api/v7/property-ai/mastery-v263/exam")
    def exam(limit:int=Query(500,ge=1,le=1000)):
        return JSONResponse(real_exam(engine,limit))

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "route":route,
        "regression":"/api/v7/property-ai/mastery-v263/regression",
        "sample":"/api/v7/property-ai/mastery-v263/sample?limit=500",
        "exam":"/api/v7/property-ai/mastery-v263/exam?limit=500"
    }
