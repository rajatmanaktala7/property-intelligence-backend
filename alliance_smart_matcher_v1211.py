
from __future__ import annotations

import html, json, re
from fastapi import HTTPException
from sqlalchemy import text

VERSION="12.1.1-SMART-MATCHER-INTELLIGENCE"

MICRO_MARKETS={
    "SOUTH DELHI":{
        "SAKET","MALVIYA NAGAR","HAUZ KHAS","GREEN PARK","GREATER KAILASH 1","GREATER KAILASH 2",
        "GK 1","GK 2","CR PARK","CHITTARANJAN PARK","KALKAJI","DEFENCE COLONY","LAJPAT NAGAR 1",
        "LAJPAT NAGAR 2","LAJPAT NAGAR 3","LAJPAT NAGAR 4","NEW FRIENDS COLONY","OKHLA PHASE 1",
        "OKHLA PHASE 2","OKHLA PHASE 3","NEHRU PLACE","PANCHSHEEL PARK","SHIVALIK"
    },
    "CENTRAL DELHI":{"CONNAUGHT PLACE","CP","KAROL BAGH","PAHARGANJ"},
    "WEST DELHI":{"RAJOURI GARDEN","JANAKPURI","PUNJABI BAGH","TILAK NAGAR","PASCHIM VIHAR"},
    "GURUGRAM":{"GURUGRAM","GURGAON","MG ROAD","GOLF COURSE ROAD","SOHNA ROAD","UDYOG VIHAR","CYBER CITY"},
    "NOIDA":{"NOIDA","GREATER NOIDA"}
}
USE_SYNONYMS={
    "CLOUD KITCHEN":{"CLOUD KITCHEN","KITCHEN","FOOD","F&B","RESTAURANT"},
    "RESTAURANT":{"RESTAURANT","CAFE","FOOD","F&B","DINING","CLOUD KITCHEN"},
    "CAFE":{"CAFE","RESTAURANT","FOOD","F&B"},
    "BANQUET":{"BANQUET","MARRIAGE","EVENT","FUNCTION"},
    "RETAIL":{"RETAIL","SHOWROOM","SHOP","STORE","HIGH STREET"},
    "OFFICE":{"OFFICE","COMMERCIAL","CORPORATE"},
    "INDUSTRIAL":{"INDUSTRIAL","FACTORY","WAREHOUSE","SHED"},
    "WAREHOUSE":{"WAREHOUSE","GODOWN","INDUSTRIAL","SHED"},
    "RESIDENTIAL":{"RESIDENTIAL","APARTMENT","FLOOR","HOUSE","VILLA"}
}
FLOOR_MAP={
    "GF":{"GF","GROUND","GROUND FLOOR","LGF","LOWER GROUND"},
    "FF":{"FF","FIRST","FIRST FLOOR"},
    "SF":{"SF","SECOND","SECOND FLOOR"},
    "TF":{"TF","THIRD","THIRD FLOOR","TOP FLOOR"},
    "BMT":{"BMT","BASEMENT"},
}

def _txt(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _obj(v):
    if isinstance(v,dict):return v
    if not v:return {}
    try:return json.loads(v)
    except:return {}
def _upper(v): return _txt(v).upper()
def _tokens(v): return set(re.findall(r"[A-Z0-9]+",_upper(v)))
def _canonical_loc(v):
    s=_upper(v).replace("GREATER KAILASH-I","GREATER KAILASH 1").replace("GREATER KAILASH-II","GREATER KAILASH 2")
    s=s.replace("GK-I","GK 1").replace("GK-II","GK 2").replace("OKHLA-1","OKHLA PHASE 1").replace("OKHLA-2","OKHLA PHASE 2").replace("OKHLA-3","OKHLA PHASE 3")
    s=s.replace("OKHLA PHASE-I","OKHLA PHASE 1").replace("OKHLA PHASE-II","OKHLA PHASE 2").replace("OKHLA PHASE-III","OKHLA PHASE 3")
    return re.sub(r"[^A-Z0-9 ]+"," ",s).strip()
def _zone(loc):
    c=_canonical_loc(loc)
    for z,places in MICRO_MARKETS.items():
        if any(p==c or p in c or c in p for p in places):
            return z
    return ""
def _clean(row): return _obj(row.get("clean_record"))
def _first(row,*names):
    c=_clean(row)
    for src in (row,c):
        for n in names:
            v=src.get(n)
            if v not in (None,"",[],{}):return v
    return None
def _req_area_range(req):
    c=_clean(req)
    lo=c.get("area_min_sqft") or c.get("minimum_area_sqft") or c.get("min_area_sqft")
    hi=c.get("area_max_sqft") or c.get("maximum_area_sqft") or c.get("max_area_sqft")
    try:lo=float(lo) if lo not in (None,"") else None
    except:lo=None
    try:hi=float(hi) if hi not in (None,"") else None
    except:hi=None
    if lo is None and hi is None and req.get("area_sqft") not in (None,""):
        try:
            mid=float(req["area_sqft"]);lo=hi=mid
        except:pass
    if lo is not None and hi is not None and lo>hi:lo,hi=hi,lo
    return lo,hi
def _req_use(req):
    return _upper(_first(req,"intended_use","suitable_category","use_case","business_category","property_type","property_category"))
def _prop_text(p):
    c=_clean(p)
    vals=[p.get("locality"),c.get("category"),c.get("property_type"),c.get("listing_type"),
          c.get("description"),c.get("property_name"),c.get("remarks"),c.get("floor")]
    return _upper(" | ".join(_txt(x) for x in vals if x not in (None,"")))
def _req_floor(req):
    return _upper(_first(req,"floor_preference","floor","preferred_floor"))
def _floor_score(req_floor,prop_text):
    if not req_floor:return 15,["floor not constrained"]
    r=_upper(req_floor);p=_upper(prop_text)
    wanted=set()
    for key,vals in FLOOR_MAP.items():
        if any(x in r for x in vals):wanted.add(key)
    if not wanted:
        return 5,["floor preference needs verification"]
    for key in wanted:
        if any(x in p for x in FLOOR_MAP[key]):return 15,["floor match"]
    if not re.search(r"\b(GF|FF|SF|TF|BMT|GROUND|FIRST|SECOND|THIRD|BASEMENT|TOP FLOOR)\b",p):
        return 3,["property floor unknown"]
    return 0,["floor mismatch"]
def _use_score(req_use,prop_text):
    if not req_use:return 10,["use not constrained"]
    p=_upper(prop_text)
    expanded=set()
    for key,vals in USE_SYNONYMS.items():
        if key in req_use or any(v in req_use for v in vals):
            expanded|=vals
    if not expanded:expanded={req_use}
    if any(x in p for x in expanded):return 20,["use/category compatible"]
    if "COMMERCIAL" in p and any(x in req_use for x in ("RETAIL","OFFICE","RESTAURANT","CAFE","CLOUD KITCHEN")):
        return 8,["commercial shell; use must be verified"]
    return 0,["use/category mismatch"]
def _location_score(req,p):
    rl=_canonical_loc(req.get("locality") or req.get("city"));pl=_canonical_loc(p.get("locality") or p.get("city"))
    if rl and pl and rl==pl:return 25,"EXACT_LOCALITY",["exact locality"]
    if rl and pl and (rl in pl or pl in rl):return 23,"EXACT_LOCALITY",["locality text match"]
    rz,pz=_zone(rl),_zone(pl)
    if rz and rz==pz:return 17,"MICRO_MARKET_ALTERNATIVE",[f"same micro-market: {rz.title()}"]
    rc=_canonical_loc(req.get("city"));pc=_canonical_loc(p.get("city"))
    if rc and pc and rc==pc:return 8,"SAME_CITY_ALTERNATIVE",["same city"]
    return 0,"BROADER_ALTERNATIVE",["different locality"]
def _area_score(req,p):
    lo,hi=_req_area_range(req)
    try:pa=float(p.get("area_sqft")) if p.get("area_sqft") not in (None,"") else None
    except:pa=None
    if pa is None:return 0,["property area unknown"]
    if lo is None and hi is None:return 10,["area not constrained"]
    if lo is None:lo=0
    if hi is None:hi=lo
    if lo<=pa<=hi:return 20,[f"area inside requirement range {lo:g}-{hi:g} sqft"]
    target=max((lo+hi)/2,1)
    distance=min(abs(pa-lo),abs(pa-hi))/target
    if distance<=0.15:return 14,["area within 15% of range"]
    if distance<=0.30:return 8,["area within 30% of range"]
    if distance<=0.50:return 3,["area within 50% of range"]
    return 0,["area outside requirement range"]
def _availability_score(p):
    if _upper(p.get("availability_status"))=="UNAVAILABLE":return -999,["unavailable"]
    ver=_upper(p.get("verification_status"));avail=_upper(p.get("availability_status"))
    if ver=="VERIFIED" and avail=="AVAILABLE":return 10,["verified available"]
    if ver=="VERIFIED":return 7,["verified; availability recheck"]
    if avail=="AVAILABLE":return 6,["available; verification pending"]
    return 2,["availability must be verified"]
def score(req,p):
    if _upper(req.get("transaction_type"))!=_upper(p.get("transaction_type")):
        return 0,["transaction mismatch"],"REJECTED",["TRANSACTION_MISMATCH"]
    reasons=["transaction match"];blockers=[]
    total=10
    ls,tier,lr=_location_score(req,p);total+=ls;reasons+=lr
    ars,ar=_area_score(req,p);total+=ars;reasons+=ar
    us,ur=_use_score(_req_use(req),_prop_text(p));total+=us;reasons+=ur
    fs,fr=_floor_score(_req_floor(req),_prop_text(p));total+=fs;reasons+=fr
    av,avr=_availability_score(p)
    if av<0:return 0,reasons+avr,"REJECTED",["UNAVAILABLE"]
    total+=av;reasons+=avr
    if us==0 and _req_use(req):blockers.append("USE_MISMATCH")
    if fs==0 and _req_floor(req):blockers.append("FLOOR_MISMATCH")
    lo,hi=_req_area_range(req)
    if (lo is not None or hi is not None) and ars==0:blockers.append("AREA_MISMATCH")
    if total>=80 and not blockers:bucket="BEST_MATCH"
    elif total>=60 and "USE_MISMATCH" not in blockers:bucket="POSSIBLE_VERIFY"
    elif total>=45:bucket="ALTERNATIVE"
    else:bucket="REJECTED"
    return min(100,total),reasons,bucket,blockers

def _smart_match_full(engine,rid,limit=50):
    import alliance_primary_workspace_v730 as ws
    import alliance_master_integration_v720 as v720
    req=ws._requirement(engine,rid)
    if not req:raise HTTPException(404,"Requirement not found or not human VERIFIED")
    tx=req.get("transaction_type") or ""
    props=v720._search_properties(engine,tx=tx,limit=5000)
    buckets={"BEST_MATCH":[],"POSSIBLE_VERIFY":[],"ALTERNATIVE":[]}
    rejected=0
    for p in props:
        s,reasons,bucket,blockers=score(req,p)
        if bucket=="REJECTED":
            rejected+=1;continue
        item={"score":s,"base_score":s,"tier":bucket,"reasons":reasons,
              "blockers":blockers,"property":p}
        buckets[bucket].append(item)
    for k in buckets:
        buckets[k].sort(key=lambda x:(x["score"],
                                     1 if _upper(x["property"].get("verification_status"))=="VERIFIED" else 0,
                                     float(x["property"].get("area_sqft") or 0)),reverse=True)
    results=buckets["BEST_MATCH"]+buckets["POSSIBLE_VERIFY"]+buckets["ALTERNATIVE"]
    # Persist only reviewable matches. Keep existing DB contract.
    with engine.begin() as c:
        for item in results[:50]:
            p=item["property"]
            c.execute(text("""INSERT INTO pi_master_matches_v720(
                requirement_canonical_id,property_canonical_id,match_score,match_reasons,status,updated_at)
                VALUES(:r,:p,:s,CAST(:why AS JSONB),'READY_FOR_REVIEW',NOW())
                ON CONFLICT(requirement_canonical_id,property_canonical_id) DO UPDATE SET
                match_score=EXCLUDED.match_score,match_reasons=EXCLUDED.match_reasons,status='READY_FOR_REVIEW',updated_at=NOW()
            """),{"r":rid,"p":p["canonical_id"],"s":item["score"],
                  "why":json.dumps({"bucket":item["tier"],"reasons":item["reasons"],"blockers":item["blockers"]})})
    return results[:limit]

def register(core):
    import alliance_primary_workspace_v730 as ws
    import alliance_master_integration_v720 as v720
    v720._score=lambda req,p:(score(req,p)[0],score(req,p)[1])
    ws._match_full=_smart_match_full
    ws._smart_matcher_version=VERSION
    app=getattr(core,"app",None) or core
    @app.get("/api/alliance/admin/smart-matcher-1211/status")
    def status():
        return {"status":"PASS","version":VERSION,
                "transaction_gate":"HARD",
                "weights":{"location":25,"area_range":20,"use_category":20,"floor":15,"transaction":10,"availability_verification":10},
                "buckets":["BEST_MATCH","POSSIBLE_VERIFY","ALTERNATIVE","REJECTED"],
                "area_policy":"uses min/max range from requirement clean_record when available",
                "location_policy":"exact -> same micro-market -> same city -> broader",
                "availability_policy":"UNAVAILABLE rejected; UNKNOWN can only score minimal availability points",
                "assignment_guard":"UNCHANGED: approved match required before assignment"}
    return {"status":"REGISTERED","version":VERSION}
