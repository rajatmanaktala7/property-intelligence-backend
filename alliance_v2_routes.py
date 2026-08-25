import json
from fastapi import Request,HTTPException,Query
from sqlalchemy import text
from alliance_v2_schema import VERSION,setup
from alliance_v2_index import rebuild
from alliance_v2_normalize import norm

def loc_score(p,rs):
    p=norm(p)
    if p in rs:return 30
    if any(r and (r in p or p in r) for r in rs):return 26
    pt=set(p.split());return max([15*len(pt&set(r.split()))/max(1,len(pt),len(set(r.split()))) for r in rs] or [0])
def area_score(a,b,x,y):
    if any(v is None for v in [a,b,x,y]):return 0
    a,b,x,y=map(float,[a,b,x,y]);ov=max(0,min(b,y)-max(a,x));span=max(1,y-x)
    if ov>0:return min(20,12+8*ov/span)
    d=min(abs(a-y),abs(x-b));tol=max(y*.25,500);return max(0,12*(1-d/tol)) if d<tol else 0
def run_match(engine,code):
    with engine.begin() as c:
        r=c.execute(text("SELECT * FROM ai_requirement_index WHERE requirement_code=:x OR source_record_id=:x ORDER BY requirement_index_id DESC LIMIT 1"),{"x":code}).mappings().first()
        if not r:raise ValueError("Requirement not indexed")
        ls=[x[0] for x in c.execute(text("SELECT location_normalized FROM ai_requirement_location WHERE requirement_index_id=:i"),{"i":r["requirement_index_id"]}).fetchall()];out=[]
        for p in c.execute(text("SELECT * FROM ai_property_match_index WHERE match_eligible=TRUE")).mappings().all():
            rej=[]
            if r["transaction_type"]!="UNKNOWN" and p["transaction_type"]!="UNKNOWN" and r["transaction_type"]!=p["transaction_type"]:rej.append("Transaction type mismatch")
            l=loc_score(p["location_normalized"],ls)
            if ls and l<10:rej.append("Location mismatch")
            a=area_score(p["area_min_sqft"],p["area_max_sqft"],r["minimum_area_sqft"],r["maximum_area_sqft"])
            if a<5:rej.append("Area outside requirement")
            rt=set(r["requirement_types"] or []);ts=15 if not rt or p["canonical_property_type"] in rt else 5;rs=15
            if r["maximum_monthly_rent"] and p["monthly_rent"] and float(p["monthly_rent"])>float(r["maximum_monthly_rent"]):
                rs=max(0,15*(1-(float(p["monthly_rent"])/float(r["maximum_monthly_rent"])-1)/.30))
                if rs<5:rej.append("Rent materially above budget")
            hard=not rej;score=l+a+rs+ts+13;score=min(score,59) if not hard else min(score,100);status="HOT" if hard and score>=90 else "STRONG" if hard and score>=80 else "POSSIBLE" if hard and score>=70 else "WEAK"
            c.execute(text("INSERT INTO ai_match_v2(requirement_index_id,match_index_id,match_score,location_score,area_score,rent_score,type_score,hard_rule_pass,rejection_reasons,positive_reasons,status,matcher_version,updated_at) VALUES(:r,:p,:s,:l,:a,:rs,:ts,:h,CAST(:rej AS jsonb),'[]'::jsonb,:st,:v,NOW()) ON CONFLICT(requirement_index_id,match_index_id) DO UPDATE SET match_score=EXCLUDED.match_score,location_score=EXCLUDED.location_score,area_score=EXCLUDED.area_score,rent_score=EXCLUDED.rent_score,type_score=EXCLUDED.type_score,hard_rule_pass=EXCLUDED.hard_rule_pass,rejection_reasons=EXCLUDED.rejection_reasons,status=EXCLUDED.status,matcher_version=EXCLUDED.matcher_version,updated_at=NOW()"),{"r":r["requirement_index_id"],"p":p["match_index_id"],"s":score,"l":l,"a":a,"rs":rs,"ts":ts,"h":hard,"rej":json.dumps(rej),"st":status,"v":VERSION})
            if hard and score>=60:out.append({"score":round(score,2),"status":status,"source":p["source_name"],"source_type":p["source_type"],"property_name":p["property_name"],"location":p["location_raw"],"area_min_sqft":p["area_min_sqft"],"area_max_sqft":p["area_max_sqft"],"rent_psf_month":p["rent_psf_month"],"monthly_rent":p["monthly_rent"],"verification_status":p["verification_status"],"data_confidence":p["data_confidence_score"]})
        return sorted(out,key=lambda x:x["score"],reverse=True)

def register(core):
    app,engine=core.app,core.engine
    @app.on_event("startup")
    def start():setup(engine)
    @app.middleware("http")
    async def no_delete(req:Request,call_next):
        if req.method.upper()=="DELETE" and (req.url.path.startswith("/api/") or any(x in req.url.path.lower() for x in ["property","requirement","contact","newspaper"])):
            return core.JSONResponse(status_code=405,content={"detail":"Delete is disabled. Correct, archive or mark inactive instead; source data is preserved."})
        return await call_next(req)
    @app.get("/api/v2/intelligence/health")
    def health(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:return {"version":VERSION,"non_destructive_policy":True,"manual_frontend_changed":False,"property_index":c.execute(text("SELECT COUNT(*) FROM ai_property_match_index")).scalar() or 0,"match_eligible_properties":c.execute(text("SELECT COUNT(*) FROM ai_property_match_index WHERE match_eligible=TRUE")).scalar() or 0,"requirements_index":c.execute(text("SELECT COUNT(*) FROM ai_requirement_index")).scalar() or 0,"history_events":c.execute(text("SELECT COUNT(*) FROM ai_source_history")).scalar() or 0}
    @app.post("/api/v2/intelligence/rebuild-index")
    def build(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        return {"status":"ok","non_destructive":True,"result":rebuild(engine)}
    @app.post("/api/v2/intelligence/match/{code}")
    def match(code:str,req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        try:return {"requirement_code":code,"matches":run_match(engine,code)}
        except ValueError as e:raise HTTPException(404,str(e))
    @app.get("/api/v2/intelligence/matches/{code}")
    def results(code:str,req:Request,minimum_score:float=Query(60,ge=0,le=100)):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:return {"requirement_code":code,"matches":[dict(x._mapping) for x in c.execute(text("SELECT m.match_score,m.status,p.property_name,p.location_raw,p.area_min_sqft,p.area_max_sqft,p.rent_psf_month,p.monthly_rent,p.source_type,p.source_name,p.verification_status,p.data_confidence_score,p.source_record_id FROM ai_match_v2 m JOIN ai_requirement_index r ON r.requirement_index_id=m.requirement_index_id JOIN ai_property_match_index p ON p.match_index_id=m.match_index_id WHERE (r.requirement_code=:x OR r.source_record_id=:x) AND m.match_score>=:s ORDER BY m.match_score DESC"),{"x":code,"s":minimum_score}).fetchall()]}
    return app
