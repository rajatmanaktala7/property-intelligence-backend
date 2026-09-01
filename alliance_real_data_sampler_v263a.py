from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

import alliance_real_data_sampler_v263 as v263
import alliance_semantic_context_mastery_v262e as v262e
import alliance_context_recovery_academy_v262d as v262d
import alliance_excellence_mastery_v262c as v262c
import alliance_boundary_context_mastery_v262b as v262b
import alliance_real_mastery_v262a as v262a

VERSION="2.6.3A-SCHEMA-AWARE-REAL-SAMPLER"
MODE="READ_ONLY_SCHEMA_AWARE_MASTER_SAMPLER"

TEXT_CANDIDATES=[
    "raw_text","message_text","raw_message","message","content","body",
    "source_text","original_text","clean_text","description","property_text",
    "remarks","details","title"
]

LOCATION_CANDIDATES=[
    "location","locality","city","micro_market","micromarket","sector",
    "project_location","property_location","address"
]

AREA_LIKE_NAMES={
    "area","size","plot_size","carpet_area","builtup_area","built_up_area",
    "super_area","saleable_area","covered_area"
}

NON_LOCATION_VALUE_RE=re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:SQ\s*FT|SQFT|SQ\s*YD|SQYD|SYD|SYDS|GAJ|YARDS?|"
    r"ACRES?|MTR|METER|METRE|SQM|SQ\s*M|FT|FEET)\s*$",re.I
)

def _q(name:str)->str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",name or ""):
        raise ValueError("Unsafe identifier")
    return '"' + name + '"'

def _columns(engine, table_name:str)->List[Dict[str,str]]:
    sql=text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema=current_schema()
          AND table_name=:table_name
        ORDER BY ordinal_position
    """)
    with engine.connect() as conn:
        rows=conn.execute(sql,{"table_name":table_name}).mappings().all()
    return [{"column_name":r["column_name"],"data_type":r["data_type"]} for r in rows]

def _textual(cols):
    return [c["column_name"] for c in cols if str(c["data_type"]).lower() in {
        "text","character varying","character","varchar"
    }]

def _existing_case_insensitive(names, preferred):
    lower={n.lower():n for n in names}
    out=[]
    for p in preferred:
        if p.lower() in lower:
            out.append(lower[p.lower()])
    return out

def _column_profile(engine, table_name:str, col:str, sample_limit:int=250)->Dict[str,Any]:
    qt=_q(table_name); qc=_q(col)
    sql=text(f"""
        SELECT {qc}::text AS v
        FROM {qt}
        WHERE {qc} IS NOT NULL
          AND length(trim({qc}::text)) > 0
        ORDER BY md5({qc}::text)
        LIMIT :n
    """)
    with engine.connect() as conn:
        vals=[r[0] for r in conn.execute(sql,{"n":sample_limit}).all()]

    nonempty=[str(v).strip() for v in vals if v is not None and str(v).strip()]
    if not nonempty:
        return {"column":col,"count":0,"distinct":0,"avg_len":0.0,"long_ratio":0.0,"area_like_ratio":0.0}

    distinct=len(set(v.lower() for v in nonempty))
    avg=sum(len(v) for v in nonempty)/len(nonempty)
    long_ratio=sum(1 for v in nonempty if len(v)>=20)/len(nonempty)
    area_like=sum(1 for v in nonempty if NON_LOCATION_VALUE_RE.match(v))/len(nonempty)
    return {
        "column":col,
        "count":len(nonempty),
        "distinct":distinct,
        "avg_len":round(avg,2),
        "long_ratio":round(long_ratio,4),
        "area_like_ratio":round(area_like,4)
    }

def discover_schema(engine, table_name:str="pi_whatsapp_property_master")->Dict[str,Any]:
    cols=_columns(engine,table_name)
    if not cols:
        return {"status":"TABLE_NOT_FOUND","table":table_name,"columns":[]}

    names=[c["column_name"] for c in cols]
    textual=_textual(cols)

    preferred_text=_existing_case_insensitive(textual,TEXT_CANDIDATES)
    fallback_text=[n for n in textual if re.search(r"(text|message|content|description|body|detail|remark|title|raw)",n,re.I)]
    text_candidates=[]
    for n in preferred_text+fallback_text:
        if n not in text_candidates:
            text_candidates.append(n)

    text_profiles=[_column_profile(engine,table_name,n) for n in text_candidates[:12]]
    # Prefer richer, diverse columns. Description with only a few unique rows should not win.
    ranked_text=sorted(
        text_profiles,
        key=lambda p:(p["distinct"],p["long_ratio"],p["avg_len"]),
        reverse=True
    )
    chosen_text=ranked_text[0]["column"] if ranked_text else None

    preferred_loc=_existing_case_insensitive(textual,LOCATION_CANDIDATES)
    # Never use generic "area" as a location field. In this DB it contains values such as 3300 sqft.
    preferred_loc=[n for n in preferred_loc if n.lower() not in AREA_LIKE_NAMES]

    loc_profiles=[_column_profile(engine,table_name,n) for n in preferred_loc[:10]]
    valid_loc=[p for p in loc_profiles if p["area_like_ratio"] < 0.20 and p["avg_len"] <= 120]
    chosen_loc=valid_loc[0]["column"] if valid_loc else None

    return {
        "status":"PASS",
        "table":table_name,
        "columns":[c["column_name"] for c in cols],
        "textual_columns":textual,
        "text_profiles":ranked_text,
        "location_profiles":loc_profiles,
        "chosen_text_column":chosen_text,
        "chosen_location_column":chosen_loc
    }

def sampler(engine,limit:int=500)->Dict[str,Any]:
    table="pi_whatsapp_property_master"
    schema=discover_schema(engine,table)
    if schema["status"]!="PASS":
        return {**schema,"rows":[],"sampled":0,"target_reached":False,"writes":0}

    text_col=schema.get("chosen_text_column")
    loc_col=schema.get("chosen_location_column")
    if not text_col:
        return {
            "status":"TEXT_COLUMN_NOT_FOUND",
            "schema":schema,
            "rows":[],"sampled":0,"target_reached":False,"writes":0
        }

    qt=_q(table); qc=_q(text_col)
    loc_expr=f"{_q(loc_col)}::text" if loc_col else "NULL::text"

    # Pull a much larger deterministic window from the true chosen text source.
    fetch_limit=min(max(limit*10,2000),15000)
    sql=text(f"""
        SELECT {qc}::text AS raw_text,
               {loc_expr} AS upstream_location
        FROM {qt}
        WHERE {qc} IS NOT NULL
          AND length(trim({qc}::text)) >= 20
        ORDER BY md5({qc}::text)
        LIMIT :fetch_limit
    """)

    with engine.connect() as conn:
        raw_rows=conn.execute(sql,{"fetch_limit":fetch_limit}).mappings().all()

    seen=set()
    out=[]
    rejected_area_location=0
    for r in raw_rows:
        raw=v262e.norm_text(r["raw_text"])
        if not raw:
            continue
        key=re.sub(r"\s+"," ",raw).strip().lower()
        if key in seen:
            continue
        seen.add(key)

        loc=(r.get("upstream_location") or "").strip() or None
        if loc and NON_LOCATION_VALUE_RE.match(loc):
            rejected_area_location+=1
            loc=None

        out.append({"raw":raw,"upstream_location":loc})
        if len(out)>=limit:
            break

    return {
        "status":"PASS",
        "table":table,
        "text_column":text_col,
        "location_column":loc_col,
        "schema_profile":schema,
        "rows":out,
        "sampled":len(out),
        "target_reached":len(out)>=limit,
        "rejected_area_like_location_values":rejected_area_location,
        "writes":0
    }

def real_exam(engine,limit:int=500)->Dict[str,Any]:
    sample=sampler(engine,limit)
    if sample["status"]!="PASS":
        return {
            "status":"SAMPLER_NOT_READY",
            "version":VERSION,
            "sampler":sample,
            "read_only":True,
            "canonical_writes":0,
            "offer_writes":0,
            "matcher_writes":0,
            "whatsapp_live_writes":0
        }

    stats=Counter()
    examples=[]
    for row in sample["rows"]:
        r=v263.coach(row["raw"],row.get("upstream_location"))
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
        if row.get("upstream_location"):
            stats["VALID_UPSTREAM_LOCATION_VALUES"]+=1

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
    old=v263.regression()
    e=v262e.regression(); d=v262d.regression(); c=v262c.regression(); b=v262b.regression(); a=v262a.regression()
    gate=all(x["critical_failures"]==0 for x in [reg,old,e,d,c,b,a])

    return {
        "status":"PASS" if gate else "TRAINING_REQUIRED",
        "version":VERSION,
        "requested_limit":limit,
        "distinct_real_candidates_examined":sample["sampled"],
        "target_reached":sample["target_reached"],
        "source_table":sample["table"],
        "source_text_column":sample["text_column"],
        "source_location_column":sample["location_column"],
        "schema_profile":sample["schema_profile"],
        "rejected_area_like_location_values":sample["rejected_area_like_location_values"],
        "stats":dict(stats),
        "examples":examples,
        "v263a_score":reg["score"],
        "v263_score":old["score"],
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
        "claim":"V263A validates schema semantics before sampling. Generic area fields are never treated as locations. PASS is not write permission."
    }

CASES=[
    (
        "AREA_VALUE_IS_NOT_LOCATION",
        "3300 sqft",
        lambda: bool(NON_LOCATION_VALUE_RE.match("3300 sqft"))
    ),
    (
        "REAL_LOCATION_TEXT_ALLOWED",
        "Vagator",
        lambda: not bool(NON_LOCATION_VALUE_RE.match("Vagator"))
    ),
    (
        "OLD_V263_REGRESSION_STILL_GREEN",
        "",
        lambda: v263.regression()["critical_failures"]==0
    )
]

def regression()->Dict[str,Any]:
    out=[]
    for key,raw,fn in CASES:
        ok=bool(fn())
        out.append({"case_key":key,"passed":ok})
    passed=sum(1 for x in out if x["passed"]); total=len(out)
    return {
        "status":"PASS" if passed==total else "FAIL",
        "version":VERSION,
        "total":total,
        "passed":passed,
        "failed":total-passed,
        "score":round(100*passed/total,2),
        "critical_failures":total-passed,
        "results":out,
        "writes_performed":0
    }

def register(core):
    app=core.app
    engine=core.engine
    route="/api/v7/property-ai/mastery-v263a/status"

    if any(getattr(r,"path",None)==route for r in app.router.routes):
        return {"status":"ALREADY_REGISTERED","version":VERSION,"route":route}

    @app.get(route)
    def status():
        return JSONResponse({
            "status":"READY","version":VERSION,"mode":MODE,"read_only":True,
            "canonical_writes":0,"offer_writes":0,
            "matcher_modified":False,"whatsapp_live_modified":False
        })

    @app.get("/api/v7/property-ai/mastery-v263a/schema")
    def schema():
        return JSONResponse(discover_schema(engine))

    @app.get("/api/v7/property-ai/mastery-v263a/sample")
    def sample(limit:int=Query(500,ge=1,le=1000)):
        s=sampler(engine,limit)
        return JSONResponse({**s,"rows":s.get("rows",[])[:25]})

    @app.get("/api/v7/property-ai/mastery-v263a/regression")
    def reg():
        return JSONResponse(regression())

    @app.get("/api/v7/property-ai/mastery-v263a/exam")
    def exam(limit:int=Query(500,ge=1,le=1000)):
        return JSONResponse(real_exam(engine,limit))

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "route":route,
        "schema":"/api/v7/property-ai/mastery-v263a/schema",
        "sample":"/api/v7/property-ai/mastery-v263a/sample?limit=500",
        "regression":"/api/v7/property-ai/mastery-v263a/regression",
        "exam":"/api/v7/property-ai/mastery-v263a/exam?limit=500"
    }
