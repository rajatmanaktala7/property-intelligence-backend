import alliance_deal_match_ai_v60 as m

def p(desc,loc,tx,fam,sub=None,area=2800,price=300000):
    return {
      "description":desc,"location_raw":loc,"transaction_raw":tx,"property_type_raw":fam,
      "subtype_raw":sub or "","area":area,"price":price,"price_text":str(price),
      "contact":"X · 9999999999","source_bucket":"Manual","source_table":"manual_properties",
      "record_id":"1","source_name":"Manual","verification":"VERIFIED","captured_on":None,
      "frontage":30,"floor":"GROUND","parking":None,"nearby_brands":None,"suitable_for":sub
    }

def run():
    req=m.parse_requirement("commercial restaurant for rent in Saket 2500-3000 sqft budget 4 lakh frontage 25 ft","SMART")
    assert req["location"]=="SAKET"
    assert req["transaction"]=="RENT"
    assert req["family"]=="COMMERCIAL"
    assert req["subtype"]=="RESTAURANT"

    good=m.normalize_candidate(p("restaurant space","Saket","rent","commercial","restaurant"))
    ok,cls,why=m.eligibility(req,good,False)
    assert ok and cls=="EXACT"

    wrongloc=m.normalize_candidate(p("restaurant space","Kalkaji","rent","commercial","restaurant"))
    ok,reason,why=m.eligibility(req,wrongloc,False)
    assert not ok and reason=="WRONG_LOCATION"

    wrongtx=m.normalize_candidate(p("restaurant space","Saket","sale","commercial","restaurant"))
    ok,reason,why=m.eligibility(req,wrongtx,False)
    assert not ok and reason=="WRONG_TRANSACTION"

    wrongtype=m.normalize_candidate(p("3 bhk apartment","Saket","rent","residential","apartment"))
    ok,reason,why=m.eligibility(req,wrongtype,False)
    assert not ok and reason=="WRONG_PROPERTY_TYPE"

    nearby=m.normalize_candidate(p("restaurant space","Malviya Nagar","rent","commercial","restaurant"))
    ok,cls,why=m.eligibility(req,nearby,True)
    assert ok and cls=="NEARBY"

if __name__=="__main__":
    run()
    print("V6 MATCHER TESTS: PASS")
