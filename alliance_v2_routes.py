import json
from fastapi import Request,HTTPException,Query
from sqlalchemy import text
from alliance_v2_schema import VERSION,setup
from alliance_v2_index import rebuild
from alliance_v2_normalize import norm
from alliance_v2_whatsapp_purity import purity_rows

LOCATION_ALIASES={
    "cp":["connaught place","connaught circus","rajiv chowk","inner circle","outer circle"],
    "connaught place":["cp","connaught circus","rajiv chowk","inner circle","outer circle"],
    "gk":["greater kailash","gk 1","gk 2","greater kailash 1","greater kailash 2"],
    "south delhi":["greater kailash","gk","defence colony","kailash colony","hauz khas","saket","malviya nagar","lajpat nagar","south extension","vasant kunj"],
}

def expand_locations(rs):
    out=set()
    for raw in rs:
        n=norm(raw)
        if not n:continue
        out.add(n)
        for k,vals in LOCATION_ALIASES.items():
            if n==k or n in vals:
                out.add(k);out.update(vals)
    return list(out)

def loc_score(p,rs):
    p=norm(p)
    rs=expand_locations(rs)
    if not p:return 0
    if p in rs:return 30
    if any(r and (r in p or p in r) for r in rs):return 27
    pt=set(p.split())
    best=0
    for r in rs:
        rt=set(r.split())
        if not rt:continue
        score=18*len(pt&rt)/max(1,len(pt),len(rt))
        best=max(best,score)
    return round(best,2)

def area_score(a,b,x,y):
    if any(v is None for v in [a,b,x,y]):return 0,"UNKNOWN"
    a,b,x,y=map(float,[a,b,x,y])
    ov=max(0,min(b,y)-max(a,x))
    req_span=max(1,y-x)
    if ov>0:
        pct=ov/max(1,min(b-a if b>a else req_span,req_span))
        return round(min(20,14+6*pct),2),"OVERLAP"
    center_req=(x+y)/2
    center_prop=(a+b)/2
    deviation=abs(center_prop-center_req)/max(1,center_req)
    if deviation<=0.10:
        return round(max(10,14-(deviation/0.10)*4),2),"NEAR_10"
    if deviation<=0.20:
        return round(max(5,10-(deviation-0.10)/0.10*5),2),"NEAR_20"
    return 0,"OUT"

def type_score(prop_type,req_types):
    req=set(req_types or [])
    p=str(prop_type or "")
    if not req:return 10
    if p in req:return 10
    compatible={
        "RETAIL_SHOP":{"RESTAURANT","CAFE","HIGH_STREET_RETAIL","MALL_RETAIL"},
        "RESTAURANT":{"RETAIL_SHOP","HIGH_STREET_RETAIL"},
        "HIGH_STREET_RETAIL":{"RETAIL_SHOP","RESTAURANT","CAFE"},
        "COMMERCIAL":{"OFFICE","RETAIL_SHOP"},
    }
    if any(r in compatible.get(p,set()) or p in compatible.get(r,set()) for r in req):return 7
    return 3

def floor_score(prop_floor,required_floor):
    if not required_floor:return 5,None
    if not prop_floor:return 2,"Floor needs verification"
    if prop_floor==required_floor:return 5,None
    return 0,f"Floor mismatch: requires {required_floor}"

def frontage_score(prop_front,min_front):
    if not min_front:return 5,None
    if prop_front is None:return 2,"Frontage needs verification"
    if float(prop_front)>=float(min_front):return 5,None
    return 0,f"Frontage below minimum {min_front:g} ft"

def suitability_score(prop_suitable,req_suitable):
    if not req_suitable:return 5,None
    if not prop_suitable:return 2,"Use/suitability needs verification"
    a=norm(prop_suitable);b=norm(req_suitable)
    if any(x in a for x in b.split() if len(x)>2):return 5,None
    return 1,"Use/suitability not confirmed"

def classify(score,hard_fail,verify_reasons):
    if hard_fail:
        return "REJECTED","DO_NOT_USE"
    if verify_reasons:
        if score>=70:return "VERIFY","VERIFY_BEFORE_SHARING"
        return "NEAR","REVIEW"
    if score>=90:return "EXACT","READY_FOR_REVIEW"
    if score>=80:return "STRONG","READY_FOR_REVIEW"
    if score>=70:return "NEAR","REVIEW"
    return "WEAK","REVIEW"

def save_gap(c,r,reason):
    c.execute(text("""INSERT INTO ai_inventory_gap(
      requirement_index_id,requirement_code,company_name,locations,transaction_type,property_types,
      minimum_area_sqft,maximum_area_sqft,minimum_frontage_ft,required_floor,suitable_for,reason,status,updated_at)
      VALUES(:i,:code,:co,:loc,:tr,CAST(:types AS jsonb),:amin,:amax,:front,:floor,:suit,:reason,'OPEN',NOW())
      ON CONFLICT(requirement_index_id) DO UPDATE SET
      requirement_code=EXCLUDED.requirement_code,company_name=EXCLUDED.company_name,
      locations=EXCLUDED.locations,transaction_type=EXCLUDED.transaction_type,
      property_types=EXCLUDED.property_types,minimum_area_sqft=EXCLUDED.minimum_area_sqft,
      maximum_area_sqft=EXCLUDED.maximum_area_sqft,minimum_frontage_ft=EXCLUDED.minimum_frontage_ft,
      required_floor=EXCLUDED.required_floor,suitable_for=EXCLUDED.suitable_for,
      reason=EXCLUDED.reason,status='OPEN',updated_at=NOW()"""),
      {"i":r["requirement_index_id"],"code":r["requirement_code"],"co":r["company_name"],
       "loc":r["preferred_locations_raw"],"tr":r["transaction_type"],
       "types":json.dumps(r["requirement_types"] or []),"amin":r["minimum_area_sqft"],
       "amax":r["maximum_area_sqft"],"front":r["minimum_frontage_ft"],"floor":r["required_floor"],
       "suit":r["suitable_for"],"reason":reason})

def close_gap(c,rid):
    c.execute(text("UPDATE ai_inventory_gap SET status='RESOLVED',updated_at=NOW() WHERE requirement_index_id=:i"),{"i":rid})

def run_match(engine,code):
    with engine.begin() as c:
        r=c.execute(text("""SELECT * FROM ai_requirement_index
        WHERE requirement_code=:x OR source_record_id=:x
        ORDER BY requirement_index_id DESC LIMIT 1"""),{"x":code}).mappings().first()
        if not r:raise ValueError("Requirement not indexed")
        ls=[x[0] for x in c.execute(text("""SELECT location_normalized FROM ai_requirement_location
        WHERE requirement_index_id=:i"""),{"i":r["requirement_index_id"]}).fetchall()]
        out=[];all_rows=[]
        for p in c.execute(text("SELECT * FROM ai_property_match_index WHERE match_eligible=TRUE")).mappings().all():
            hard=[];verify=[];positive=[]
            rtx=r["transaction_type"];ptx=p["transaction_type"]
            if rtx!="UNKNOWN" and ptx!="UNKNOWN" and rtx!=ptx and ptx!="LEASE_OR_SALE" and rtx!="LEASE_OR_SALE":
                hard.append("Transaction type mismatch")
            l=loc_score(p["location_normalized"],ls)
            if ls and l<10:hard.append("Location mismatch")
            elif l>=27:positive.append("Location aligned")
            a,aband=area_score(p["area_min_sqft"],p["area_max_sqft"],r["minimum_area_sqft"],r["maximum_area_sqft"])
            if aband=="OUT":hard.append("Area materially outside requirement")
            elif aband=="NEAR_20":verify.append("Area outside preferred range")
            elif aband in {"OVERLAP","NEAR_10"}:positive.append("Area aligned or near-fit")
            rs=15
            if r["maximum_monthly_rent"] and p["monthly_rent"]:
                ratio=float(p["monthly_rent"])/float(r["maximum_monthly_rent"])
                if ratio>1.30:hard.append("Rent materially above budget");rs=0
                elif ratio>1.10:verify.append("Rent above preferred budget");rs=8
                elif ratio>1:rs=12
                else:positive.append("Rent within budget")
            elif r["maximum_monthly_rent"] and not p["monthly_rent"]:
                verify.append("Rent needs verification");rs=8
            ts=type_score(p["canonical_property_type"],r["requirement_types"])
            fs,fmsg=floor_score(p["floor_normalized"],r["required_floor"])
            if fmsg:
                if fs==0:hard.append(fmsg)
                else:verify.append(fmsg)
            frs,frmsg=frontage_score(p["frontage_ft"],r["minimum_frontage_ft"])
            if frmsg:
                if frs==0:hard.append(frmsg)
                else:verify.append(frmsg)
            ss,smsg=suitability_score(p["suitable_for"],r["suitable_for"])
            if smsg:verify.append(smsg)
            base=l+a+rs+ts+fs+frs+ss+10
            score=round(min(100,base),2)
            status,action=classify(score,hard,verify)
            if hard:score=min(score,59)
            c.execute(text("""INSERT INTO ai_match_v2(
              requirement_index_id,match_index_id,match_score,location_score,area_score,rent_score,
              type_score,floor_score,frontage_score,suitability_score,hard_rule_pass,
              rejection_reasons,positive_reasons,status,action,matcher_version,updated_at)
              VALUES(:r,:p,:s,:l,:a,:rs,:ts,:fs,:frs,:ss,:h,CAST(:rej AS jsonb),CAST(:pos AS jsonb),
              :st,:act,:v,NOW())
              ON CONFLICT(requirement_index_id,match_index_id) DO UPDATE SET
              match_score=EXCLUDED.match_score,location_score=EXCLUDED.location_score,
              area_score=EXCLUDED.area_score,rent_score=EXCLUDED.rent_score,type_score=EXCLUDED.type_score,
              floor_score=EXCLUDED.floor_score,frontage_score=EXCLUDED.frontage_score,
              suitability_score=EXCLUDED.suitability_score,hard_rule_pass=EXCLUDED.hard_rule_pass,
              rejection_reasons=EXCLUDED.rejection_reasons,positive_reasons=EXCLUDED.positive_reasons,
              status=EXCLUDED.status,action=EXCLUDED.action,matcher_version=EXCLUDED.matcher_version,
              updated_at=NOW()"""),
              {"r":r["requirement_index_id"],"p":p["match_index_id"],"s":score,"l":l,"a":a,"rs":rs,
               "ts":ts,"fs":fs,"frs":frs,"ss":ss,"h":not hard,"rej":json.dumps(hard+verify),
               "pos":json.dumps(positive),"st":status,"act":action,"v":VERSION})
            item={
              "score":score,"status":status,"action":action,"source":p["source_name"],
              "source_type":p["source_type"],"property_name":p["property_name"],
              "location":p["location_raw"],"area_min_sqft":p["area_min_sqft"],
              "area_max_sqft":p["area_max_sqft"],"rent_psf_month":p["rent_psf_month"],
              "monthly_rent":p["monthly_rent"],"transaction_type":p["transaction_type"],
              "canonical_property_type":p["canonical_property_type"],"floor":p["floor_raw"],
              "frontage_ft":p["frontage_ft"],"verification_status":p["verification_status"],
              "data_confidence":p["data_confidence_score"],"reasons":hard+verify,
              "positive_reasons":positive,"source_record_id":p["source_record_id"]
            }
            all_rows.append(item)
            if status in {"EXACT","STRONG","NEAR","VERIFY"} and score>=65:
                out.append(item)
        out=sorted(out,key=lambda x:(x["status"]=="EXACT",x["status"]=="STRONG",x["score"]),reverse=True)
        actionable=[x for x in out if x["status"] in {"EXACT","STRONG","VERIFY"} and x["score"]>=70]
        if actionable:
            close_gap(c,r["requirement_index_id"])
            gap=None
        else:
            reason=f"No actionable {r['transaction_type']} property found for {r['preferred_locations_raw']} in {r['minimum_area_sqft']}-{r['maximum_area_sqft']} sqft."
            save_gap(c,r,reason)
            gap={
              "status":"OPEN","requirement_code":r["requirement_code"],"company_name":r["company_name"],
              "locations":r["preferred_locations_raw"],"transaction_type":r["transaction_type"],
              "minimum_area_sqft":r["minimum_area_sqft"],"maximum_area_sqft":r["maximum_area_sqft"],
              "minimum_frontage_ft":r["minimum_frontage_ft"],"required_floor":r["required_floor"],
              "suitable_for":r["suitable_for"],"reason":reason
            }
        near_rejected=sorted([x for x in all_rows if x["status"]=="REJECTED"],key=lambda x:x["score"],reverse=True)[:10]
        return {"matches":out[:50],"inventory_gap":gap,"top_rejected":near_rejected}

def register(core):
    app,engine=core.app,core.engine


    @app.middleware("http")
    async def no_delete(req:Request,call_next):
        if req.method.upper()=="DELETE" and (req.url.path.startswith("/api/") or any(x in req.url.path.lower() for x in ["property","requirement","contact","newspaper"])):
            return core.JSONResponse(status_code=405,content={"detail":"Delete is disabled. Correct, archive or mark inactive instead; source data is preserved."})
        return await call_next(req)

    @app.get("/api/v2/intelligence/health")
    def health(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:
            return {
              "version":VERSION,"non_destructive_policy":True,"manual_frontend_changed":False,
              "property_index":c.execute(text("SELECT COUNT(*) FROM ai_property_match_index")).scalar() or 0,
              "match_eligible_properties":c.execute(text("SELECT COUNT(*) FROM ai_property_match_index WHERE match_eligible=TRUE")).scalar() or 0,
              "requirements_index":c.execute(text("SELECT COUNT(*) FROM ai_requirement_index")).scalar() or 0,
              "open_inventory_gaps":c.execute(text("SELECT COUNT(*) FROM ai_inventory_gap WHERE status='OPEN'")).scalar() or 0,
              "history_events":c.execute(text("SELECT COUNT(*) FROM ai_source_history")).scalar() or 0
            }

    @app.post("/api/v2/intelligence/rebuild-index")
    def build(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        return {"status":"ok","non_destructive":True,"result":rebuild(engine)}

    @app.post("/api/v2/intelligence/match/{code}")
    def match(code:str,req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        try:
            result=run_match(engine,code)
            return {"requirement_code":code,**result}
        except ValueError as e:
            raise HTTPException(404,str(e))

    @app.get("/api/v2/intelligence/matches/{code}")
    def results(code:str,req:Request,minimum_score:float=Query(0,ge=0,le=100)):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:
            rows=[dict(x._mapping) for x in c.execute(text("""SELECT
              m.match_score,m.status,m.action,m.hard_rule_pass,m.rejection_reasons,m.positive_reasons,
              m.location_score,m.area_score,m.rent_score,m.type_score,m.floor_score,m.frontage_score,
              m.suitability_score,p.property_name,p.location_raw,p.area_min_sqft,p.area_max_sqft,
              p.rent_psf_month,p.monthly_rent,p.transaction_type,p.canonical_property_type,p.floor_raw,
              p.frontage_ft,p.source_type,p.source_name,p.verification_status,p.data_confidence_score,
              p.source_record_id
              FROM ai_match_v2 m
              JOIN ai_requirement_index r ON r.requirement_index_id=m.requirement_index_id
              JOIN ai_property_match_index p ON p.match_index_id=m.match_index_id
              WHERE (r.requirement_code=:x OR r.source_record_id=:x) AND m.match_score>=:s
              ORDER BY m.match_score DESC"""),{"x":code,"s":minimum_score}).fetchall()]
            gap=c.execute(text("""SELECT * FROM ai_inventory_gap g JOIN ai_requirement_index r
            ON r.requirement_index_id=g.requirement_index_id
            WHERE (r.requirement_code=:x OR r.source_record_id=:x) LIMIT 1"""),{"x":code}).mappings().first()
            return {"requirement_code":code,"matches":rows,"inventory_gap":dict(gap) if gap else None}

    @app.get("/api/v2/intelligence/inventory-gaps")
    def gaps(req:Request,status:str=Query("OPEN")):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:
            rows=[dict(x._mapping) for x in c.execute(text("""SELECT * FROM ai_inventory_gap
            WHERE (:s='ALL' OR status=:s) ORDER BY updated_at DESC LIMIT 500"""),{"s":status.upper()}).fetchall()]
            return {"status":status.upper(),"rows":rows}


    @app.get("/api/v2/intelligence/whatsapp-purity")
    def whatsapp_purity(req:Request,status:str=Query("ALL"),limit:int=Query(200,ge=1,le=1000)):
        if hasattr(core,"need_login"):core.need_login(req)
        rows=purity_rows(engine,status,limit)
        summary={}
        for r in rows:
            k=r.get("review_status") or "UNKNOWN"
            summary[k]=summary.get(k,0)+1
        return {"status":status.upper(),"count":len(rows),"summary":summary,"rows":rows}

    return app


