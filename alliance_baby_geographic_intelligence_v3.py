from __future__ import annotations
import re
VERSION="3.0.0-ALLIANCE-BABY-GEOGRAPHIC-COMPARABLE-INTELLIGENCE"

def _norm(v): return re.sub(r"\\s+"," ",str(v or "").strip()).upper()
def _loc(row): return str(row.get("location") or row.get("locality") or "").strip()

def _market_graph(req):
    import alliance_micromarket_knowledge_v1 as geo
    origin=req.get("locality") or req.get("location") or ""
    raw=" ".join(str(req.get(k) or "") for k in ("raw_text","category","purpose","property_type"))
    report=geo.suggest(origin,raw,12) if origin else {"status":"UNKNOWN_LOCATION","suggestions":[]}
    return report,{_norm(s.get("location")):s for s in report.get("suggestions") or []}

def classify_geography(req,row):
    import alliance_micromarket_knowledge_v1 as geo
    origin=req.get("locality") or req.get("location") or ""; target=_loc(row)
    if not origin or not target:
        return {"class":"BROAD_SEARCH","reason":"location evidence incomplete","recommendable":False}
    om=geo.market(origin); tm=geo.market(target)
    if _norm(origin)==_norm(target):
        return {"class":"EXACT","reason":"same canonical micro-market","recommendable":True}
    report,graph=_market_graph(req); hit=graph.get(_norm(target))
    if hit and hit.get("fit")=="USE_MATCH":
        explicit=bool(om and any(_norm(x)==_norm(target) for x in (om[7] or [])))
        return {"class":"COMPARABLE","reason":hit.get("reason") or "micro-market graph use match",
                "recommendable":True,"distance_km":hit.get("distance_km"),
                "explicit_graph_edge":explicit,"market_type":hit.get("market_type")}
    if om and tm and _norm(om[2])==_norm(tm[2]):
        return {"class":"SAME_CITY_SEARCH","reason":"same city but not certified comparable for this requirement","recommendable":False}
    return {"class":"BROAD_SEARCH","reason":"not supported by comparable-market graph","recommendable":False}

def curate(req,rows):
    exact=[]; comparable=[]; search_only=[]
    for row in rows or []:
        g=classify_geography(req,row); item=dict(row)
        item.update({"geo_class":g["class"],"geo_reason":g["reason"],"geo_recommendable":g["recommendable"],
                     "geo_distance_km":g.get("distance_km"),"geo_market_type":g.get("market_type"),
                     "geo_explicit_graph_edge":bool(g.get("explicit_graph_edge"))})
        if g["class"]=="EXACT": exact.append(item)
        elif g["class"]=="COMPARABLE": comparable.append(item)
        else: search_only.append(item)
    def rank(x):
        return (int(x.get("trust_score") or 0),
                {"EXPLICIT_FIT":3,"POSSIBLE_COMMERCIAL":2,"UNKNOWN":1}.get(x.get("use_fit"),0),
                {"STRONG":4,"GOOD":3,"BROAD":2,"UNKNOWN":1}.get(x.get("area_band"),0),
                1 if x.get("geo_explicit_graph_edge") else 0,
                float(x.get("relevance_score") or 0))
    exact.sort(key=rank,reverse=True); comparable.sort(key=rank,reverse=True)
    return {"exact":exact,"comparable":comparable,"search_only":search_only}

def decide(req,rows):
    c=curate(req,rows)
    exact_safe=[x for x in c["exact"] if x.get("client_safe")]
    comp_safe=[x for x in c["comparable"] if x.get("client_safe")]
    if exact_safe: decision="VERIFIED_EXACT_RESULTS"
    elif c["exact"]: decision="EXACT_CANDIDATES_NEED_VERIFICATION"
    elif comp_safe: decision="VERIFIED_COMPARABLE_ALTERNATIVES"
    elif c["comparable"]: decision="COMPARABLE_ALTERNATIVES_NEED_VERIFICATION"
    else: decision="NO_RELEVANT_MASTER_INVENTORY"
    return {"decision":decision,"exact":c["exact"],"comparable":c["comparable"],"search_only":c["search_only"],
            "client_safe_exact":exact_safe,"client_safe_comparable":comp_safe,
            "counts":{"exact":len(c["exact"]),"comparable":len(c["comparable"]),"search_only":len(c["search_only"]),
                      "client_safe_exact":len(exact_safe),"client_safe_comparable":len(comp_safe)}}

def exam():
    import alliance_baby_cre_copilot_v1 as baby
    tests=[]
    def add(name,ok,detail=""): tests.append({"name":name,"pass":bool(ok),"detail":detail})
    cases=[
      ("Saket FNB","Need 2000 sqft restaurant on lease in Saket","Malviya Nagar","COMPARABLE"),
      ("Saket to Mumbai","Need 2000 sqft restaurant on lease in Saket","Bandra West","BROAD_SEARCH"),
      ("BKC office","Need 3000 sqft office for sale in BKC","Lower Parel","COMPARABLE"),
      ("BKC to Delhi","Need 3000 sqft office for sale in BKC","Connaught Place","BROAD_SEARCH"),
      ("Siolim villa","Want villa for sale in Siolim 4000 sqft","Assagao","COMPARABLE"),
      ("Siolim to Delhi","Want villa for sale in Siolim 4000 sqft","Saket","BROAD_SEARCH"),
      ("Sector 18 retail","Need 900 sqft showroom for rent in Sector 18 Noida","Sector 38A Noida","COMPARABLE"),
      ("Cyber City office","Need 2500 sqft office on lease in DLF Cyber City","Golf Course Road","COMPARABLE")]
    for name,q,target,want in cases:
        req=baby._parse_free_text(q); got=classify_geography(req,{"location":target})
        add(name,got["class"]==want,f"{target}: {got['class']} expected {want}")
    req=baby._parse_free_text("Need 2000 sqft restaurant on lease in Saket")
    got=classify_geography(req,{"location":"Karol Bagh","city":"DELHI"})
    add("same-city unrelated is search-only",not got["recommendable"],got["class"])
    d=decide(req,[{"location":"Bandra West","city":"MUMBAI","client_safe":False,"trust_score":20,
                   "use_fit":"EXPLICIT_FIT","area_band":"STRONG","relevance_score":90}])
    add("unrelated inventory cannot create alternatives",d["decision"]=="NO_RELEVANT_MASTER_INVENTORY",d["decision"])
    passed=sum(1 for x in tests if x["pass"])
    return {"status":"PASS" if passed==len(tests) else "FAIL","tested":len(tests),"passed":passed,
            "failed":len(tests)-passed,"tests":tests}

def self_test():
    r=exam()
    assert r["status"]=="PASS",r
    return True
