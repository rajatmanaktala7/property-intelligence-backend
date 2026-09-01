from __future__ import annotations
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_excellence_mastery_v262c as v262c
import alliance_boundary_context_mastery_v262b as v262b
import alliance_real_mastery_v262a as v262a
import alliance_property_evidence_grammar_v257b as v257b

VERSION="2.6.2D1-REAL-CONTEXT-RECOVERY-CONFLICT-FIX"
MODE="READ_ONLY_CONTEXT_RECOVERY_DISTINCT_SAMPLING_SHADOW"

RENT_STRONG_RE=re.compile(r"\b(?:FOR\s+RENT|ASKING\s+RENT|RENT\s*(?:IS|@|RS|₹)?|LEASE|LEASING|MONTHLY\s+RENT|RENTAL)\b",re.I)
SALE_STRONG_RE=re.compile(r"\b(?:FOR\s+SALE|ASKING\s+PRICE|SALE\s+PRICE|OWNER\s+(?:WANTS|WILLING)\s+TO\s+SELL|SELLING|DEMAND\s+(?:RS|₹|\d)|PRICE\s+(?:RS|₹|\d))\b",re.I)
REQUIREMENT_STRONG_RE=re.compile(r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|BUYER\s+REQUIREMENT|PURCHASE\s+REQUIREMENT|RENTAL\s+REQUIREMENT)\b",re.I)
INCIDENTAL_REQ_RE=re.compile(r"\b(?:AS\s+PER\s+CLIENT\s+REQUIREMENT|AS\s+PER\s+REQUIREMENT)\b",re.I)

LOCATION_RE=re.compile(
 r"\b(?:DLF\s+PHASE\s+\d|SUSHANT\s+LOK\s*\d*|DWARKA|KALKAJI|SAKET|NOIDA|GURGAON|GURUGRAM|"
 r"VAGATOR|ANJUNA|SIOLIM|ASSAGAO|LOKHANDWALA(?:\s+BACK\s+ROAD)?|JUHU|SECTOR\s+\d+[A-Z]?|"
 r"GREATER\s+KAILASH\s*\d*|GK\s*[12]|VASANT\s+KUNJ|VASANT\s+VIHAR|DEFENCE\s+COLONY|"
 r"NEHRU\s+PLACE|CONNAUGHT\s+PLACE|ROHINI|PITAMPURA|JANAKPURI|RAJOURI\s+GARDEN)\b",
 re.I
)

def _norm(v:Any)->str:
    return re.sub(r"\s+"," ",str(v or "")).strip()

def _unique(seq):
    out=[]
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out

def _find_locations(text:str)->List[str]:
    return _unique([_norm(m.group(0)).title() for m in LOCATION_RE.finditer(text or "")])

def _context_candidates(raw:str, entity:Dict[str,Any])->List[str]:
    vals=[]
    vals.extend(_find_locations(raw))
    vals.extend(_find_locations(str(entity.get("inherited_header") or "")))
    vals.extend(_find_locations(str(entity.get("text") or "")))
    if entity.get("location"):
        vals.append(entity.get("location"))
    return _unique(vals)

def _recover_transaction(entity:Dict[str,Any])->Tuple[Optional[str],List[str]]:
    text=str(entity.get("text") or "")
    lessons=[]
    rent=bool(RENT_STRONG_RE.search(text))
    sale=bool(SALE_STRONG_RE.search(text))
    req=bool(REQUIREMENT_STRONG_RE.search(text)) and not bool(INCIDENTAL_REQ_RE.search(text))
    cls=entity.get("classification")

    if cls=="REQUIREMENT" or req:
        return None, lessons

    current=entity.get("transaction")
    if current in {"RENT","SALE"}:
        return current, lessons

    if rent and not sale:
        lessons.append("TRANSACTION_RECOVERED_FROM_STRONG_RENT_EVIDENCE")
        return "RENT", lessons
    if sale and not rent:
        lessons.append("TRANSACTION_RECOVERED_FROM_STRONG_SALE_EVIDENCE")
        return "SALE", lessons
    return None, lessons

def _is_requirement_entity(entity:Dict[str,Any])->bool:
    return entity.get("classification")=="REQUIREMENT"

def coach(text:str)->Dict[str,Any]:
    base=v262c.coach(text)
    raw=str(text or "")
    entities=[dict(e) for e in base.get("entities") or []]
    lessons=list(base.get("lessons") or [])
    unresolved=[]

    # Start from V262C unresolved issues but remove a generic transaction issue
    # when it belongs to a REQUIREMENT. Requirements do not require transaction
    # direction to be valid review candidates.
    base_unresolved=list(base.get("unresolved") or [])
    for issue in base_unresolved:
        keep=True
        m=re.match(r"ENTITY_(\d+)_TRANSACTION$",str(issue))
        if m:
            idx=int(m.group(1))-1
            if 0 <= idx < len(entities) and _is_requirement_entity(entities[idx]):
                keep=False
                lessons.append("REQUIREMENT_TRANSACTION_NOT_REQUIRED")
        if keep:
            unresolved.append(issue)

    for idx,e in enumerate(entities):
        locs=_context_candidates(raw,e)

        # Critical fix: competing explicit locations are a conflict even if an
        # upstream layer already selected the first one.
        if len(locs)>1:
            e["location_conflict_candidates"]=locs
            e["location_conflict"]=True
            if "LOCATION_CONTEXT_CONFLICT" not in (e.get("blockers") or []):
                e.setdefault("blockers",[]).append("LOCATION_CONTEXT_CONFLICT")
            unresolved.append(f"ENTITY_{idx+1}_LOCATION_CONFLICT")
            lessons.append("CONFLICTING_LOCATION_CONTEXT_BLOCKED")
        else:
            e["location_conflict_candidates"]=locs
            e["location_conflict"]=False
            if not e.get("location") and len(locs)==1:
                e["location"]=locs[0]
                e["blockers"]=[b for b in (e.get("blockers") or []) if b!="LOCATION_UNRESOLVED"]
                lessons.append("LOCATION_RECOVERED_FROM_CONVERGENT_CONTEXT")
            elif not e.get("location"):
                unresolved.append(f"ENTITY_{idx+1}_LOCATION")

        recovered_tx,tx_lessons=_recover_transaction(e)
        lessons.extend(tx_lessons)

        if not _is_requirement_entity(e):
            if e.get("transaction") is None and recovered_tx:
                e["transaction"]=recovered_tx
                e["offer_state"]="SINGLE_RENT_OFFER" if recovered_tx=="RENT" else "SINGLE_SALE_OFFER"
                e["blockers"]=[b for b in (e.get("blockers") or []) if b!="TRANSACTION_UNRESOLVED"]

        if _is_requirement_entity(e):
            req_ready=bool(
                REQUIREMENT_STRONG_RE.search(e.get("text") or "")
                and not e.get("location_conflict")
                and (e.get("location") or len(locs)==1)
            )
            e["requirement_review_ready_shadow"]=req_ready
            e["availability_write_ready_shadow"]=False
            if not req_ready:
                unresolved.append(f"ENTITY_{idx+1}_REQUIREMENT_INCOMPLETE")
        else:
            blockers=set(e.get("blockers") or [])
            availability_ready=bool(
                e.get("location")
                and not e.get("location_conflict")
                and e.get("transaction") in {"RENT","SALE"}
                and "PACKAGE_PRICE_NOT_INDIVIDUAL_PRICE" not in blockers
                and "BOUNDARY_REVIEW_REQUIRED" not in blockers
                and "LOCATION_CONTEXT_CONFLICT" not in blockers
            )
            e["availability_write_ready_shadow"]=availability_ready
            e["requirement_review_ready_shadow"]=False
            if not availability_ready:
                if not e.get("transaction"):
                    unresolved.append(f"ENTITY_{idx+1}_TRANSACTION")
                if "PACKAGE_PRICE_NOT_INDIVIDUAL_PRICE" in blockers:
                    unresolved.append(f"ENTITY_{idx+1}_PACKAGE_PRICE")
                if "BOUNDARY_REVIEW_REQUIRED" in blockers:
                    unresolved.append(f"ENTITY_{idx+1}_BOUNDARY")

    if base.get("package_offer"):
        unresolved.append("PACKAGE_OFFER_REQUIRES_RELATIONSHIP_REVIEW")

    if (
        base.get("multi_unit_signal")
        and len(entities)==1
        and entities[0].get("offer_state")=="MULTIPLE_OFFERS_UNRESOLVED"
    ):
        unresolved.append("MULTI_UNIT_DUAL_OFFER_REQUIRES_OFFER_REVIEW")

    unresolved=sorted(set(unresolved))
    availability_entities=[e for e in entities if not _is_requirement_entity(e)]
    requirement_entities=[e for e in entities if _is_requirement_entity(e)]

    availability_gate=bool(
        availability_entities
        and all(e.get("availability_write_ready_shadow") for e in availability_entities)
        and not base.get("package_offer")
        and not (
            base.get("multi_unit_signal")
            and any(e.get("offer_state")=="MULTIPLE_OFFERS_UNRESOLVED" for e in availability_entities)
        )
    )

    requirement_gate=bool(
        requirement_entities
        and all(e.get("requirement_review_ready_shadow") for e in requirement_entities)
    )

    return {
      "relationship_type":base.get("relationship_type"),
      "entity_count":len(entities),
      "entities":entities,
      "package_offer":base.get("package_offer"),
      "multi_unit_signal":base.get("multi_unit_signal"),
      "lessons":sorted(set(lessons)),
      "unresolved":unresolved,
      "availability_gate_passed_shadow":availability_gate,
      "requirement_gate_passed_shadow":requirement_gate,
      "safe_for_controlled_write_shadow":availability_gate,
      "safe_for_requirement_review_shadow":requirement_gate,
      "canonical_writes":0,
      "offer_writes":0,
      "database_write":False
    }

CASES=[
 ("REQUIREMENT_WITHOUT_TRANSACTION_IS_READY",
  "Looking for 3000 SQFT in Saket for restaurant budget 5 Lakh",
  lambda r:r["requirement_gate_passed_shadow"] and not r["availability_gate_passed_shadow"]
           and "ENTITY_1_TRANSACTION" not in r["unresolved"]),
 ("RENT_RECOVERY",
  "DLF PHASE 2 | 4 BHK | 400 SYDS | Asking rent 1.60 Lac",
  lambda r:r["entities"][0]["transaction"]=="RENT" and r["availability_gate_passed_shadow"]),
 ("SALE_RECOVERY",
  "SAKET | 2500 SQFT | Owner wants to sell | Demand 8 Cr",
  lambda r:r["entities"][0]["transaction"]=="SALE" and r["availability_gate_passed_shadow"]),
 ("CONFLICTING_LOCATIONS_BLOCK",
  "Saket / Kalkaji | 2000 SQFT | For Rent 4 Lakh",
  lambda r:not r["availability_gate_passed_shadow"]
           and r["entities"][0]["location_conflict"]
           and "ENTITY_1_LOCATION_CONFLICT" in r["unresolved"]),
 ("PACKAGE_REMAINS_BLOCKED",
  "LOKHANDWALA BACK ROAD | BUNGALOW 1 | 3854 Carpet | 5 BHK | BUNGALOW 2 | 2656 Carpet | 6 BHK | Both Bungalows: 70 Cr",
  lambda r:not r["availability_gate_passed_shadow"] and r["package_offer"] is not None),
 ("MULTI_UNIT_DUAL_OFFER_REMAINS_BLOCKED",
  "2bhk 2set each story | Total 8 set of 2bhk | With lift Rent 2 lac | 130 sqyd plot size | Demand 3.80 cr | New Pg",
  lambda r:not r["availability_gate_passed_shadow"]),
 ("NORMAL_RENT_REMAINS_SAFE",
  "DLF PHASE 2 | 4 BHK | 400 SYDS | Fully Furnished | Rent 1.60 Lac",
  lambda r:r["availability_gate_passed_shadow"]),
 ("INCIDENTAL_REQUIREMENT_AVAILABILITY",
  "DLF PHASE 2 | 4 BHK | Fully Furnished as per client requirement | Urgent Rent Owner Going Abroad",
  lambda r:r["entities"][0]["classification"]=="AVAILABILITY")
]

def regression()->Dict[str,Any]:
    out=[]
    for key,text,fn in CASES:
        r=coach(text)
        ok=bool(fn(r))
        out.append({"case_key":key,"passed":ok,"result":r})
    p=sum(1 for x in out if x["passed"])
    t=len(out)
    return {
      "status":"PASS" if p==t else "FAIL",
      "version":VERSION,
      "total":t,
      "passed":p,
      "failed":t-p,
      "score":round(100*p/t,2),
      "critical_failures":t-p,
      "results":out,
      "writes_performed":0
    }

def _distinct_real_candidates(engine,limit:int)->List[Dict[str,Any]]:
    raw=v257b._benchmark(engine,min(max(limit,500),1000))
    out=[]
    seen=set()
    for burst in raw.get("bursts") or []:
        for c in burst.get("candidates") or []:
            text=_norm(c.get("own_text_redacted") or "")
            key=(text,c.get("classification"),c.get("transaction"),c.get("location"))
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(c)
            if len(out)>=limit:
                return out
    return out

def exam(engine,limit:int=500)->Dict[str,Any]:
    candidates=_distinct_real_candidates(engine,limit)
    stats=Counter()
    examples=[]

    for c in candidates:
        r=coach(str(c.get("own_text_redacted") or ""))

        if r["availability_gate_passed_shadow"]:
            stats["AVAILABILITY_READY"]+=1
        if r["requirement_gate_passed_shadow"]:
            stats["REQUIREMENT_READY"]+=1
        if not r["availability_gate_passed_shadow"] and not r["requirement_gate_passed_shadow"]:
            stats["REVIEW_REQUIRED"]+=1

        for lesson in r["lessons"]:
            stats["LESSON_"+lesson]+=1

        for issue in r["unresolved"]:
            if "LOCATION" in issue:
                stats["UNRESOLVED_LOCATION"]+=1
            if "TRANSACTION" in issue:
                stats["UNRESOLVED_TRANSACTION"]+=1
            if "PACKAGE" in issue:
                stats["UNRESOLVED_PACKAGE"]+=1
            if "BOUNDARY" in issue:
                stats["UNRESOLVED_BOUNDARY"]+=1

        if (
            not r["availability_gate_passed_shadow"]
            and not r["requirement_gate_passed_shadow"]
            and len(examples)<100
        ):
            examples.append({
              "raw":c.get("own_text_redacted"),
              "upstream_classification":c.get("classification"),
              "upstream_transaction":c.get("transaction"),
              "upstream_location":c.get("location"),
              "coached":r
            })

    reg=regression()
    a=v262a.regression()
    b=v262b.regression()
    c=v262c.regression()

    gate=bool(
        reg["critical_failures"]==0
        and a["critical_failures"]==0
        and b["critical_failures"]==0
        and c["critical_failures"]==0
    )

    return {
      "status":"PASS" if gate else "TRAINING_REQUIRED",
      "version":VERSION,
      "requested_limit":limit,
      "distinct_real_candidates_examined":len(candidates),
      "target_reached":len(candidates)>=limit,
      "stats":dict(stats),
      "examples":examples,
      "v262d_score":reg["score"],
      "v262d_critical_failures":reg["critical_failures"],
      "v262c_score":c["score"],
      "v262c_critical_failures":c["critical_failures"],
      "v262b_score":b["score"],
      "v262b_critical_failures":b["critical_failures"],
      "v262a_score":a["score"],
      "v262a_critical_failures":a["critical_failures"],
      "mastery_gate_passed":gate,
      "read_only":True,
      "canonical_writes":0,
      "offer_writes":0,
      "matcher_writes":0,
      "whatsapp_live_writes":0,
      "claim":"PASS means all known V262A/B/C/D1 safety regressions pass. target_reached reports actual distinct real-record coverage."
    }

def register(core):
    app=core.app
    engine=core.engine
    route="/api/v7/property-ai/mastery-v262d/status"

    if any(getattr(r,"path",None)==route for r in app.router.routes):
        return {"status":"ALREADY_REGISTERED","version":VERSION,"route":route}

    @app.get(route)
    def status():
        return JSONResponse({
          "status":"READY",
          "version":VERSION,
          "mode":MODE,
          "read_only":True,
          "canonical_writes":0,
          "offer_writes":0,
          "matcher_modified":False,
          "whatsapp_live_modified":False
        })

    @app.get("/api/v7/property-ai/mastery-v262d/regression")
    def reg():
        return JSONResponse(regression())

    @app.get("/api/v7/property-ai/mastery-v262d/exam")
    def mastery(limit:int=Query(500,ge=1,le=1000)):
        return JSONResponse(exam(engine,limit))

    return {
      "status":"REGISTERED",
      "version":VERSION,
      "route":route,
      "regression":"/api/v7/property-ai/mastery-v262d/regression",
      "exam":"/api/v7/property-ai/mastery-v262d/exam?limit=500"
    }
