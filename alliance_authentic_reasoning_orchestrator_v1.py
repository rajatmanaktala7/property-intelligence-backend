from __future__ import annotations
import json, re
from datetime import datetime, timezone
from sqlalchemy import text
VERSION="1.0.0-AUTHENTIC-REASONING-ORCHESTRATOR"

def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip()).upper()
def _txt(req): return " ".join(str(req.get(k) or "") for k in ("raw_text","location","city","transaction","purpose","category","property_type","subtype","area_min","area_max","budget_max"))
def interpret(req):
    s=_norm(_txt(req))
    use="GENERAL"
    if any(x in s for x in ("RESTAURANT","CAFE","F&B","FNB","BAR","LOUNGE","CLUB")): use="FNB"
    elif any(x in s for x in ("RETAIL","SHOP","STORE","SHOWROOM","BRAND")): use="RETAIL"
    elif "OFFICE" in s: use="OFFICE"
    elif any(x in s for x in ("VILLA","APARTMENT","FLAT","RESIDENTIAL","HOUSE")): use="RESIDENTIAL"
    tx="RENT" if any(x in s for x in ("RENT","LEASE")) else ("SALE" if any(x in s for x in ("SALE","BUY","PURCHASE")) else _norm(req.get("transaction")))
    return {"use":use,"transaction":tx or "UNKNOWN","location":req.get("location") or req.get("primary_location") or "","raw":_txt(req)}

def evidence_status(p):
    verified=_norm(p.get("verification_status"))=="VERIFIED"
    availability=_norm(p.get("availability_status"))
    source=bool(p.get("source_type") or p.get("source_url") or p.get("source_id"))
    if verified and availability=="AVAILABLE" and source:return "STRONG"
    if verified and availability=="AVAILABLE":return "VERIFIED"
    if verified:return "VERIFIED_AVAILABILITY_UNKNOWN"
    return "NEEDS_VERIFICATION"

def search_plan(req, reason=""):
    q=interpret(req); loc=q["location"]; use=q["use"]
    plan=[
      {"priority":1,"channel":"MASTER_DATABASE","query":f"{use} {q['transaction']} {loc}","why":"Canonical inventory first"},
      {"priority":2,"channel":"MICROMARKET_GRAPH","query":f"Comparable {use} markets near {loc}","why":"Use-aware geographic alternatives"},
      {"priority":3,"channel":"INTERNAL_SOURCE_EVIDENCE","query":f"{use} {loc} WhatsApp magazine newspaper manual","why":"Search unpromoted evidence without treating it as verified inventory"},
      {"priority":4,"channel":"PROPERTY_DISCOVERY","query":f"{use} property {loc} {q['transaction']}","why":"Fresh public-web property discovery"},
      {"priority":5,"channel":"COMMERCIAL_INTELLIGENCE","query":f"{use} leasing expansion {loc}","why":"Market/occupier intelligence and new supply"},
      {"priority":6,"channel":"HUMAN_VERIFICATION","query":f"Call/source-check shortlisted {loc} options","why":"Required before client-safe availability claim"},
    ]
    return {"reason":reason or "No acceptable canonical match","plan":plan}

def reason(req, matches):
    q=interpret(req); out=[]
    for m in matches or []:
        p=m.get("property") or m
        score=float(m.get("score") or 0); tier=m.get("match_tier") or "UNKNOWN"
        ev=evidence_status(p)
        confidence=max(0,min(100,score+(8 if ev=="STRONG" else 4 if ev.startswith("VERIFIED") else -12)))
        out.append({"canonical_id":p.get("canonical_id"),"location":p.get("location") or p.get("primary_location"),
                    "score":score,"tier":tier,"evidence":ev,"confidence":round(confidence,1),
                    "client_safe":ev in ("STRONG","VERIFIED") and tier!="TRANSACTION_AREA_ALTERNATIVE",
                    "why":m.get("reasons") or []})
    out.sort(key=lambda x:(not x["client_safe"],-x["confidence"]))
    exact=[x for x in out if x["tier"]=="EXACT_LOCALITY"]
    alternatives=[x for x in out if x["tier"]!="EXACT_LOCALITY"]
    if exact:
        decision="EXACT_RESULTS"
    elif alternatives:
        decision="ALTERNATIVES_WITH_EXPLANATION"
    else:
        decision="SEARCH_ESCALATION"
    return {"version":VERSION,"interpreted":q,"decision":decision,"exact":exact[:10],"alternatives":alternatives[:10],
            "search_escalation":search_plan(req) if decision=="SEARCH_ESCALATION" else None,
            "truth_rules":["Never invent inventory","Unknown is not verified","Availability must be explicit","Source evidence is not Master inventory","Explain alternatives"]}

def self_test():
    a=interpret({"raw_text":"Need 2000 sqft restaurant on lease in Saket","location":"Saket"})
    assert a["use"]=="FNB" and a["transaction"]=="RENT"
    r=reason({"raw_text":"restaurant Saket","location":"Saket"},[])
    assert r["decision"]=="SEARCH_ESCALATION" and len(r["search_escalation"]["plan"])>=5
    return True
