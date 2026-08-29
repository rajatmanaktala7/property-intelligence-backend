from __future__ import annotations

from collections import Counter
from fastapi import Query
from fastapi.responses import HTMLResponse, JSONResponse
import alliance_deal_match_ai_v60 as base

VERSION="6.1.0-PERCENT-PLUS-HIDDEN-SECOND-PREFERENCE"
_ORIGINAL_RUN_MATCH=base.run_match

CITY_ONLY={"GURUGRAM","GURGAON","DELHI","NEW DELHI","DELHI NCR","NCR","NOIDA","GREATER NOIDA",
           "FARIDABAD","GHAZIABAD","GOA","NORTH GOA","SOUTH GOA","MUMBAI","BENGALURU","BANGALORE"}

def _hidden_reason(item,duplicate_ids):
    loc=base.norm(item.get("location"))
    tx=base.norm(item.get("transaction"))
    rid=str(item.get("record_id") or "")
    source_table=base.norm(item.get("source_table"))
    verification=base.norm(item.get("verification"))
    price=item.get("price")
    area=item.get("area")

    if "REVIEW" in verification or "NOISE" in verification or "REJECT" in verification:
        return "Source record is under review"
    if loc in CITY_ONLY:
        return "Only city is known; micro-location/project missing"
    if rid and duplicate_ids.get(rid,0)>1 and "WHATSAPP" in source_table:
        return "Same WhatsApp source produced multiple conflicting fragments"
    try:
        p=float(price) if price not in (None,"") else None
    except Exception:
        p=None
    if tx=="RENT" and p is not None and (p<1000 or p>5_000_000):
        return "Rent amount/unit requires verification"
    if tx=="SALE" and p is not None and 0<p<1_000_000:
        return "Sale price/unit requires verification"
    try:
        a=float(area) if area not in (None,"") else None
    except Exception:
        a=None
    if a is not None and a<=0:
        return "Area requires verification"
    return None

def run_match_v61(core, requirement_text, mode="SMART", min_score=70.0, limit=100,
                  include_hidden=False, hidden_min_score=80.0):
    # Ask V6.0 to score eligible inventory first, then apply V6.1 visibility policy.
    raw=_ORIGINAL_RUN_MATCH(core,requirement_text,mode,0.0,max(limit*4,300))
    pool=list(raw.get("exact",[]))+list(raw.get("nearby",[]))
    duplicate_ids=Counter(str(x.get("record_id") or "") for x in pool if x.get("record_id"))

    exact=[]; nearby=[]; hidden=[]
    for item in pool:
        score=float(item.get("match_score") or 0)
        reason=_hidden_reason(item,duplicate_ids)
        if reason:
            if include_hidden and score>=float(hidden_min_score):
                x=dict(item)
                x["hidden_reason"]=reason
                x["share_policy"]="SECOND_PREFERENCE_VERIFY_BEFORE_SHARE"
                hidden.append(x)
            continue
        if score<float(min_score):
            continue
        if item.get("match_class")=="NEARBY":
            nearby.append(item)
        else:
            exact.append(item)

    exact.sort(key=lambda x:(x.get("match_score",0),x.get("deal_probability",0)),reverse=True)
    nearby.sort(key=lambda x:(x.get("match_score",0),x.get("deal_probability",0)),reverse=True)
    hidden.sort(key=lambda x:(x.get("match_score",0),x.get("deal_probability",0)),reverse=True)

    raw["version"]=VERSION
    raw["exact"]=exact[:limit]
    raw["nearby"]=nearby[:limit]
    raw["hidden_second_preference"]=hidden[:limit]
    raw["summary"]["exact_matches"]=len(exact)
    raw["summary"]["nearby_alternatives"]=len(nearby)
    raw["summary"]["hidden_second_preference"]=len(hidden)
    raw["summary"]["inventory_gap"]=len(exact)==0
    raw["matching_policy"]={
        "minimum_match_percent":float(min_score),
        "hidden_enabled":bool(include_hidden),
        "hidden_minimum_match_percent":float(hidden_min_score),
        "primary_excludes_review":True,
        "hidden_never_mixed_with_primary":True,
        "hidden_share_requires_verification":True,
    }
    return raw

def _hidden_table(rows):
    trs=[]
    for r in rows:
        provenance=f"{r.get('source_bucket')} / {r.get('source_table')} / {r.get('source_name') or ''}"
        why="; ".join(r.get("why") or [])
        missing=", ".join(r.get("missing") or [])
        trs.append(f"""<tr>
          <td><b>{base.esc(r.get('match_score'))}%</b></td>
          <td>{base.esc(r.get('location') or 'Unknown')}</td>
          <td>{base.esc(r.get('transaction') or 'Unknown')}</td>
          <td>{base.esc(r.get('family') or 'Unknown')} / {base.esc(r.get('subtype') or 'Generic')}</td>
          <td class=desc>{base.esc(r.get('description'))}</td>
          <td>{base.esc(r.get('area'))}</td>
          <td>{base.esc(r.get('price_text') or r.get('price'))}</td>
          <td>{base.esc(r.get('contact'))}</td>
          <td>{base.esc(provenance)}</td>
          <td><b>VERIFY FIRST:</b> {base.esc(r.get('hidden_reason'))}</td>
          <td>{base.esc(why)}</td><td>{base.esc(missing)}</td>
        </tr>""")
    return "".join(trs)

def render_form_v61():
    body="""<div class=card><h2>High-Accuracy Property Matcher V6.1</h2>
      <p class=muted>Primary results contain clean inventory only. Hidden/review inventory can be requested separately as a second preference.</p>
      <form method=get action='/deal-match-ai-v60'>
      <div class=grid>
        <div style='grid-column:1/-1'><label>Requirement</label><textarea name=q rows=4 required></textarea></div>
        <div><label>Location Mode</label><select name=mode>
          <option value=SMART selected>SMART - exact first + separate nearby</option>
          <option value=STRICT>STRICT - exact location only</option>
          <option value=EXPANSION>EXPANSION - exact + nearby options</option></select></div>
        <div><label>Primary Minimum Match %</label><input type=number name=min_score value=70 min=40 max=100></div>
        <div><label>Hidden 2nd Preference Minimum %</label><input type=number name=hidden_min_score value=80 min=40 max=100></div>
        <div><label><input type=checkbox name=include_hidden value=1 style='width:auto'> Include hidden/review as 2nd preference</label>
        <div class=muted>Hidden properties never enter primary results and must be verified before sharing.</div></div>
        <div style='align-self:end'><button>Run AI Matcher</button></div>
      </div></form></div>"""
    return HTMLResponse(base._page("Alliance Deal Match AI V6.1",body))

def render_results_v61(core,q,mode,min_score,include_hidden=False,hidden_min_score=80):
    res=run_match_v61(core,q,mode,min_score,100,include_hidden,hidden_min_score)
    req=res["requirement"]; s=res["summary"]
    parsed=f"""<div class=grid>
      <div><b>Location</b><br>{base.esc(req.get('location') or 'Not identified')}</div>
      <div><b>Transaction</b><br>{base.esc(req.get('transaction') or 'Not identified')}</div>
      <div><b>Property Family</b><br>{base.esc(req.get('family') or 'Not identified')}</div>
      <div><b>Subtype / Use</b><br>{base.esc(req.get('subtype') or 'Generic')}</div>
      <div><b>Primary Match Cutoff</b><br>{base.esc(min_score)}%</div>
      <div><b>Hidden 2nd Preference</b><br>{"ON" if include_hidden else "OFF"} · {base.esc(hidden_min_score)}%</div>
    </div>"""
    body=f"""<div class=card><h2>Requirement Intelligence Card</h2><p>{base.esc(q)}</p>{parsed}</div>
    <div class=card><h3>Results</h3><p><b>{s['exact_matches']}</b> clean exact ·
    <b>{s['nearby_alternatives']}</b> clean nearby · <b>{s.get('hidden_second_preference',0)}</b> hidden second preference</p></div>
    <div class=card><h2>A. Primary Exact Matches</h2>
      <p class=green>Only clean inventory at or above your selected match %.</p>
      <div class=scroll><table><tr><th>Match</th><th>Deal Probability</th><th>Location</th><th>Transaction</th><th>Type</th><th>Description</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Verification</th><th>Freshness</th><th>Source</th><th>Why Matched</th><th>Missing Info</th></tr>
      {base._result_table(res['exact']) or '<tr><td colspan=14>No clean exact match at this percentage.</td></tr>'}</table></div></div>
    <div class=card><h2>B. Clean Nearby / Equivalent Alternatives</h2>
      <p class=amber>Equivalent-location options stay separate from exact results.</p>
      <div class=scroll><table><tr><th>Match</th><th>Deal Probability</th><th>Location</th><th>Transaction</th><th>Type</th><th>Description</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Verification</th><th>Freshness</th><th>Source</th><th>Why Matched</th><th>Missing Info</th></tr>
      {base._result_table(res['nearby']) or '<tr><td colspan=14>No clean nearby alternative at this percentage.</td></tr>'}</table></div></div>"""
    if include_hidden:
        body+=f"""<div class=card><h2>C. Hidden Inventory - Second Preference Only</h2>
        <p class=amber><b>AI thinks these may fit, but they are intentionally hidden from Property Availability.</b>
        Verify location, amount/unit and availability before sharing with a client.</p>
        <div class=scroll><table><tr><th>Match</th><th>Location</th><th>Transaction</th><th>Type</th>
        <th>Description</th><th>Area</th><th>Price/Rent</th><th>Internal Contact</th><th>Source</th>
        <th>Why Hidden</th><th>Why Matched</th><th>Missing</th></tr>
        {_hidden_table(res['hidden_second_preference']) or '<tr><td colspan=12>No hidden property qualifies at the selected percentage.</td></tr>'}
        </table></div></div>"""
    body+="<div class=card><a class=btn href='/deal-match-ai-v60'>Run Another Requirement</a></div>"
    return HTMLResponse(base._page("Alliance Deal Match AI V6.1 Results",body))

def register(core):
    # Monkeypatch V6.0 globals. Existing registered FastAPI route resolves these globals at request time.
    base.run_match=run_match_v61
    base.render_form=render_form_v61

    # Replace only the GET route so V6.1 can accept hidden options.
    app=core.app
    for route in list(app.router.routes):
        if getattr(route,"path",None)==base.ROUTE and "GET" in (getattr(route,"methods",set()) or set()):
            app.router.routes.remove(route)

    @app.get(base.ROUTE,response_class=HTMLResponse)
    def deal_match_page(
        q:str=Query("",max_length=5000),
        mode:str=Query("SMART"),
        min_score:float=Query(70,ge=40,le=100),
        include_hidden:int=Query(0,ge=0,le=1),
        hidden_min_score:float=Query(80,ge=40,le=100)
    ):
        if not q.strip():
            return render_form_v61()
        return render_results_v61(core,q.strip(),mode,min_score,bool(include_hidden),hidden_min_score)

    # Replace API route too.
    for route in list(app.router.routes):
        if getattr(route,"path",None)=="/api/v60/deal-match" and "GET" in (getattr(route,"methods",set()) or set()):
            app.router.routes.remove(route)

    @app.get("/api/v60/deal-match")
    def deal_match_api(
        q:str=Query(...,min_length=2,max_length=5000),
        mode:str=Query("SMART"),
        min_score:float=Query(70,ge=40,le=100),
        include_hidden:int=Query(0,ge=0,le=1),
        hidden_min_score:float=Query(80,ge=40,le=100),
        limit:int=Query(50,ge=1,le=200)
    ):
        return JSONResponse(run_match_v61(core,q,mode,min_score,limit,bool(include_hidden),hidden_min_score))

    return {"status":"REGISTERED","version":VERSION,"route":base.ROUTE}
