from __future__ import annotations
import re
from collections import Counter
from typing import Any, Dict, List
from fastapi import Query
from fastapi.responses import JSONResponse
import alliance_property_evidence_grammar_v257b as v257b
import alliance_topper_training_v261 as v261
import alliance_real_topper_coach_v261b as v261b

VERSION="2.6.2A-REAL-ALLIANCE-BRAIN-CORRECTION"
MODE="READ_ONLY_CORRECTED_INTERPRETATION_SHADOW"
RENT_RE=re.compile(r"\b(?:FOR\s+RENT|RENTAL|RENT|LEASE|LEASING)\b",re.I)
SALE_RE=re.compile(r"\b(?:FOR\s+SALE|SALE|SELL|SELLING|DEMAND\s*(?:RS\.?)?\s*\d|ASKING\s+PRICE|\d+(?:\.\d+)?\s*CR(?:ORE)?)\b",re.I)
MONEY_RE=re.compile(r"(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*(?:L|LAC|LAKH|LACS|LAKHS)(?![A-Z])",re.I)
RATE_RE=re.compile(r"\b(?:RATE\s*(?:IS\s*)?)?(?:RS\.?\s*)?\d[\d,.]*\s*(?:/|PER)\s*(?:SQ\s*FT|SQFT|SFT|FEET|SQ\s*YD|SQYD|YARD|YARDS|YD|YDS|GAJ|SQM|MTR|METER)\b",re.I)
DEMAND_RE=re.compile(r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|RENTAL\s+REQUIREMENT|PURCHASE\s+REQUIREMENT|BUYER\s+REQUIREMENT)\b",re.I)
INCIDENTAL_RE=re.compile(r"\b(?:AS\s+PER\s+CLIENT\s+REQUIREMENT|AS\s+PER\s+REQUIREMENT)\b",re.I)
AVAIL_RE=re.compile(r"\b(?:AVAILABLE|FOR\s+RENT|FOR\s+SALE|URGENT\s+RENT|URGENT\s+SALE|OWNER\s+GOING|READY\s+TO\s+MOVE|PRE[\s-]*RENTED)\b",re.I)

def correct(c:Dict[str,Any])->Dict[str,Any]:
    raw=str(c.get("own_text_redacted") or "")
    meta=dict(c.get("v257b") or {})
    integrity=dict(meta.get("record_integrity") or {})
    req=dict(meta.get("requirement_gate") or {})
    blockers=list(meta.get("hard_blockers") or [])
    cls=c.get("classification"); tx=c.get("transaction"); fixes=[]
    rent=bool(RENT_RE.search(raw)); sale=bool(SALE_RE.search(raw))
    money=MONEY_RE.findall(raw); rate=bool(RATE_RE.search(raw))
    demand=bool(DEMAND_RE.search(raw)); incidental=bool(INCIDENTAL_RE.search(raw)); avail=bool(AVAIL_RE.search(raw))
    if cls=="REQUIREMENT" and incidental and avail and not demand:
        cls="AVAILABILITY"; fixes.append("REQUIREMENT_MISROUTE_CORRECTED_TO_AVAILABILITY")
    if demand and not incidental:
        cls="REQUIREMENT"
        if c.get("classification")!="REQUIREMENT": fixes.append("DIRECTIONAL_DEMAND_CORRECTED_TO_REQUIREMENT")
    if cls=="REQUIREMENT" and req.get("complete_enough_for_requirement_review") and "TRANSACTION_MISSING" in blockers:
        blockers=[x for x in blockers if x!="TRANSACTION_MISSING"]; fixes.append("REQUIREMENT_TRANSACTION_BLOCKER_REMOVED")
    if cls=="AVAILABILITY":
        if c.get("transaction")=="SALE" and rent and not sale:
            tx="RENT"; fixes.append("UPSTREAM_SALE_CORRECTED_TO_RENT")
        elif c.get("transaction")=="RENT" and sale and not rent:
            tx="SALE"; fixes.append("UPSTREAM_RENT_CORRECTED_TO_SALE")
    if rent and sale:
        tx=None
        if "MULTIPLE_OFFERS_UNRESOLVED" not in blockers: blockers.append("MULTIPLE_OFFERS_UNRESOLVED")
        fixes.append("MIXED_RENT_SALE_PRESERVED_UNRESOLVED")
    if integrity.get("class")=="MULTIPLE_PROPERTIES_OR_MERGED":
        if "MULTIPLE_PROPERTIES_OR_MERGED" not in blockers: blockers.append("MULTIPLE_PROPERTIES_OR_MERGED")
        fixes.append("MERGED_PROPERTY_HARD_BLOCKED")
    if rate or "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in blockers:
        if "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" not in blockers: blockers.append("AMBIGUOUS_RATE_NOT_TOTAL_PRICE")
        fixes.append("RATE_TOTALIZATION_FORBIDDEN")
    old_money=int((meta.get("fact_evidence") or {}).get("money_mentions") or 0)
    if money and old_money==0: fixes.append("COMPACT_LAKH_MONEY_RECOVERED")
    return {"original":{"classification":c.get("classification"),"transaction":c.get("transaction")},
            "corrected":{"classification":cls,"transaction":tx,"hard_blockers":sorted(set(blockers)),
                         "money_mentions":max(old_money,len(money))},
            "corrections":sorted(set(fixes)),"rate_totalized":False,"offer_auto_selected":False,"database_write":False}

CASES=[
("RENT_NOT_SALE",{"classification":"AVAILABILITY","transaction":"SALE","property_family":"RESIDENTIAL","location":"DLF Phase 2","own_text_redacted":"4 BHK 3363 sqft furnished rental asking 1.5L + maintenance","v257b":{"record_integrity":{"class":"SINGLE_PROPERTY_LIKELY"},"hard_blockers":[],"fact_evidence":{"money_mentions":0}}},lambda r:r["corrected"]["transaction"]=="RENT" and "COMPACT_LAKH_MONEY_RECOVERED" in r["corrections"]),
("INCIDENTAL_REQ",{"classification":"REQUIREMENT","transaction":"RENT","property_family":"RESIDENTIAL","location":"Delhi","own_text_redacted":"3 BHK as per client requirement Ready to Move Urgent Rent Owner Going Abroad","v257b":{"record_integrity":{"class":"PROPERTY_FRAGMENT"},"hard_blockers":[],"fact_evidence":{},"requirement_gate":{"complete_enough_for_requirement_review":False}}},lambda r:r["corrected"]["classification"]=="AVAILABILITY"),
("REQ_NO_TX",{"classification":"REQUIREMENT","transaction":None,"property_family":"RESIDENTIAL","location":"Vagator","own_text_redacted":"Looking for 3/4 BHK villa Vagator Anjuna Siolim Assagao Budget 1.5 to 2.25 Lakh/month","v257b":{"record_integrity":{"class":"SINGLE_PROPERTY_LIKELY"},"hard_blockers":["TRANSACTION_MISSING"],"fact_evidence":{"money_mentions":1},"requirement_gate":{"complete_enough_for_requirement_review":True}}},lambda r:"TRANSACTION_MISSING" not in r["corrected"]["hard_blockers"] and r["corrected"]["transaction"] is None),
("MERGED",{"classification":"AVAILABILITY","transaction":"SALE","property_family":"LAND","location":None,"own_text_redacted":"2bhk 2set each story Total 8 set With lift Rent 2 lac 130 sqyd plot Demand 3.80 cr","v257b":{"record_integrity":{"class":"MULTIPLE_PROPERTIES_OR_MERGED"},"hard_blockers":[],"fact_evidence":{"money_mentions":2}}},lambda r:"MULTIPLE_PROPERTIES_OR_MERGED" in r["corrected"]["hard_blockers"] and "MULTIPLE_OFFERS_UNRESOLVED" in r["corrected"]["hard_blockers"]),
("RATE",{"classification":"AVAILABILITY","transaction":"SALE","property_family":"LAND","location":"Noida","own_text_redacted":"Plot size 500 yards Rate is rs 50000 per yards Near Jewer airport","v257b":{"record_integrity":{"class":"SINGLE_PROPERTY_LIKELY"},"hard_blockers":[],"fact_evidence":{}}},lambda r:"AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in r["corrected"]["hard_blockers"] and not r["rate_totalized"])
]

def regression():
    out=[]
    for key,c,fn in CASES:
        r=correct(c); out.append({"case_key":key,"passed":bool(fn(r)),"result":r})
    p=sum(x["passed"] for x in out); t=len(out)
    return {"status":"PASS" if p==t else "FAIL","version":VERSION,"total":t,"passed":p,"failed":t-p,
            "score":round(100*p/t,2),"critical_failures":t-p,"results":out,"writes_performed":0}

def exam(engine,limit=500):
    b=v257b._benchmark(engine,limit); counts=Counter(); examples=[]; total=0
    for burst in b.get("bursts") or []:
        for c in burst.get("candidates") or []:
            total+=1; r=correct(c)
            for f in r["corrections"]: counts[f]+=1
            if r["corrections"] and len(examples)<100:
                examples.append({"text":c.get("own_text_redacted"),"location":c.get("location"),"family":c.get("property_family"),**r})
    a=v261._academy_plus_adversarial(engine,5); g=v261b._gold_regression(); reg=regression()
    gate=bool(a.get("topper_gate_passed") and g.get("topper_gate_passed") and reg["critical_failures"]==0)
    return {"status":"PASS" if gate else "TRAINING_REQUIRED","version":VERSION,"real_candidates_examined":total,
            "correction_counts":dict(counts),"correction_examples":examples,"academy_score":a.get("score"),
            "academy_critical_failures":a.get("critical_failures"),"real_gold_score":g.get("score"),
            "real_gold_critical_failures":g.get("critical_failures"),"brain_correction_score":reg["score"],
            "brain_correction_critical_failures":reg["critical_failures"],"mastery_gate_passed":gate,
            "canonical_writes":0,"offer_writes":0,"matcher_writes":0,"whatsapp_live_writes":0,
            "claim":"PASS means known safety regressions pass while real Alliance records are examined in corrected shadow mode."}

def register(core):
    app=core.app; engine=core.engine; route="/api/v7/property-ai/mastery-v262a/status"
    if any(getattr(r,"path",None)==route for r in app.router.routes): return {"status":"ALREADY_REGISTERED","version":VERSION,"route":route}
    @app.get(route)
    def status(): return JSONResponse({"status":"READY","version":VERSION,"mode":MODE,"read_only":True,"canonical_writes":0,"offer_writes":0,"matcher_modified":False,"whatsapp_live_modified":False})
    @app.get("/api/v7/property-ai/mastery-v262a/regression")
    def reg(): return JSONResponse(regression())
    @app.get("/api/v7/property-ai/mastery-v262a/exam")
    def mastery(limit:int=Query(500,ge=1,le=1000)): return JSONResponse(exam(engine,limit))
    return {"status":"REGISTERED","version":VERSION,"route":route,"regression":"/api/v7/property-ai/mastery-v262a/regression","exam":"/api/v7/property-ai/mastery-v262a/exam?limit=500"}
