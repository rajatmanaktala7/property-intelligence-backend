from __future__ import annotations
VERSION="4.2.0-ALLIANCE-BABY-MARKET-EVIDENCE-TRUTH-GATE"

EVIDENCE_REQUIRED={"FASHION":3,"JEWELLERY":3,"LUXURY":3,"FNB":4}

def apply(req,target,base_decision,evidence):
    kind=str((evidence or {}).get("kind") or "").upper()
    count=int((evidence or {}).get("distinct_count") or 0)
    status=str((evidence or {}).get("status") or "UNKNOWN").upper()
    out=dict(base_decision or {})
    reasons=list(out.get("reasons") or [])
    required=EVIDENCE_REQUIRED.get(kind)
    if required is not None:
        if count < required or status!="STRONG":
            out["class"]="SEARCH_TARGET"
            out["recommendable"]=False
            out["score"]=min(float(out.get("score") or 0),49)
            reasons.append(f"evidence gate: {count}/{required} qualifying entities; category-cluster recommendation not certified")
        else:
            out["recommendable"]=True
            if out.get("class")=="SEARCH_TARGET":
                out["class"]="USE_CASE_COMPARABLE"
            reasons.append(f"evidence gate: {count}/{required} qualifying entities")
    out["reasons"]=reasons
    out["evidence_gate"]={"kind":kind,"status":status,"count":count,"required":required,
                          "passed":required is None or (count>=required and status=="STRONG")}
    return out

def exam():
    tests=[]
    def add(name,kind,count,status,base,want):
        got=apply({}, "X", base, {"kind":kind,"distinct_count":count,"status":status})
        tests.append({"name":name,"pass":got["class"]==want,"got":got["class"],"want":want})
    base={"class":"USE_CASE_COMPARABLE","recommendable":True,"score":80,"reasons":["static cluster"]}
    add("fashion unknown blocked","FASHION",0,"UNKNOWN",base,"SEARCH_TARGET")
    add("fashion 2 blocked","FASHION",2,"INSUFFICIENT",base,"SEARCH_TARGET")
    add("fashion 3 strong allowed","FASHION",3,"STRONG",base,"USE_CASE_COMPARABLE")
    add("jewellery unknown blocked","JEWELLERY",0,"UNKNOWN",base,"SEARCH_TARGET")
    add("luxury unknown blocked","LUXURY",0,"UNKNOWN",base,"SEARCH_TARGET")
    add("fnb 3 blocked","FNB",3,"INSUFFICIENT",base,"SEARCH_TARGET")
    add("fnb 4 strong allowed","FNB",4,"STRONG",base,"USE_CASE_COMPARABLE")
    add("office unaffected","OFFICE",0,"UNKNOWN",base,"USE_CASE_COMPARABLE")
    add("grocery unaffected","GROCERY",0,"UNKNOWN",base,"USE_CASE_COMPARABLE")
    p=sum(1 for x in tests if x["pass"])
    return {"status":"PASS" if p==len(tests) else "FAIL","tested":len(tests),"passed":p,"failed":len(tests)-p,"tests":tests}

def self_test():
    r=exam()
    assert r["status"]=="PASS",r
    return True
