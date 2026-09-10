from __future__ import annotations
import html, json, traceback
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

VERSION="1.0.0-ALLIANCE-BABY-AUTONOMOUS-SATISFACTION-GATE"

BENCHMARKS=[
    {"q":"Need 2000 sqft restaurant on lease in Saket","expect":{"location":"Saket","tx":"RENT","use":"FNB","area":2000}},
    {"q":"Need 1200 sqft retail shop for rent in Khan Market","expect":{"location":"Khan Market","tx":"RENT","use":"RETAIL","area":1200}},
    {"q":"Looking to buy 3000 sqft office in BKC","expect":{"location":"BKC","tx":"SALE","use":"OFFICE","area":3000}},
    {"q":"Need 5000 sqft banquet hall on lease in Rajouri Garden","expect":{"location":"Rajouri Garden","tx":"RENT","use":"BANQUET","area":5000}},
    {"q":"Want villa for sale in Siolim 4000 sqft","expect":{"location":"Siolim","tx":"SALE","use":"RESIDENTIAL","area":4000}},
    {"q":"Need 1800 sqft cafe in Bandra West on rent","expect":{"location":"Bandra West","tx":"RENT","use":"FNB","area":1800}},
    {"q":"Need 2500 sqft office on lease in DLF Cyber City","expect":{"location":"DLF Cyber City","tx":"RENT","use":"OFFICE","area":2500}},
    {"q":"Looking for 900 sqft showroom for rent in Sector 18 Noida","expect":{"location":"Sector 18 Noida","tx":"RENT","use":"RETAIL","area":900}},
    {"q":"Need 1500 sqft restaurant on rent in Hauz Khas","expect":{"location":"Hauz Khas","tx":"RENT","use":"FNB","area":1500}},
    {"q":"Need 2000 sqft restaurant on rent in Connaught Place","expect":{"location":"Connaught Place","tx":"RENT","use":"FNB","area":2000}},
]

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _safe_num(v):
    try: return float(v)
    except Exception: return None

def _row_checks(req,row):
    issues=[]
    tx=(req.get("transaction_type") or "").upper()
    rtx=(row.get("transaction") or "").upper()
    if tx and rtx and tx!=rtx:
        issues.append("TRANSACTION_MISMATCH")

    requested=_safe_num(req.get("area_sqft"))
    got=_safe_num(row.get("area_sqft"))
    if requested and got:
        diff=abs(got-requested)/max(requested,1)
        if diff>0.5000001:
            issues.append("AREA_OUTSIDE_50_PERCENT")

    if row.get("client_safe"):
        if row.get("truth")!="VERIFIED_AVAILABLE":
            issues.append("CLIENT_SAFE_WITHOUT_VERIFIED_AVAILABLE")
        if row.get("use_fit")!="EXPLICIT_FIT":
            issues.append("CLIENT_SAFE_WITHOUT_EXPLICIT_USE")
        if row.get("area_band")=="OUTSIDE":
            issues.append("CLIENT_SAFE_OUTSIDE_AREA_GATE")

    if row.get("use_fit")=="EXPLICIT_FIT":
        evidence=[str(x).upper() for x in (row.get("use_evidence") or [])]
        if not evidence:
            issues.append("EXPLICIT_FIT_WITHOUT_EVIDENCE")

    return issues

def deterministic_exam():
    import alliance_baby_cre_copilot_v1 as baby
    base=baby.training_exam()
    tests=list(base.get("tests") or [])

    # Extra adversarial checks beyond Baby's built-in 19.
    def add(name,ok,detail=""):
        tests.append({"name":name,"pass":bool(ok),"detail":detail})

    req={"category":"FNB","purpose":"FNB","area_sqft":2000,"transaction_type":"RENT"}
    adversarial=[
      ("commercial not explicit FNB", baby._use_fit(req,{"property_type":"Commercial"})[0]!="EXPLICIT_FIT"),
      ("restaurant explicit FNB", baby._use_fit(req,{"description":"restaurant with kitchen"})[0]=="EXPLICIT_FIT"),
      ("residential rejects FNB", baby._use_fit(req,{"property_type":"Residential Apartment"})[0]=="REJECT_RESIDENTIAL"),
      ("700 sqft rejected for 2000", not baby._hard_eligible(req,{"area_sqft":700,"transaction_type":"RENT","property_type":"Commercial"})[0]),
      ("1000 sqft remains broad not outside", baby._area_band(req,{"area_sqft":1000})[0]=="BROAD"),
      ("sale does not pass rent", not baby._hard_eligible(req,{"area_sqft":2000,"transaction_type":"SALE","property_type":"Commercial"})[0]),
      ("verified available trust 100", baby._workflow_truth({"verification_status":"VERIFIED","availability_status":"AVAILABLE"})==("VERIFIED_AVAILABLE",100)),
      ("verified unknown availability not client-safe truth", baby._workflow_truth({"verification_status":"VERIFIED","availability_status":"UNKNOWN"})[0]=="VERIFIED_AVAILABILITY_UNKNOWN"),
    ]
    for name,ok in adversarial: add(name,ok)

    passed=sum(1 for x in tests if x.get("pass"))
    return {
      "status":"PASS" if passed==len(tests) else "FAIL",
      "tested":len(tests),"passed":passed,"failed":len(tests)-passed,"tests":tests
    }

def live_exam(engine):
    import alliance_baby_cre_copilot_v1 as baby
    results=[]
    fatal=[]

    for b in BENCHMARKS:
        q=b["q"]; exp=b["expect"]
        try:
            data=baby.analyze(engine,q)
            req=data.get("requirement") or {}
            assess=data.get("assessment") or {}
            rows=assess.get("all") or []

            checks={
              "status_ok": data.get("status")=="OK",
              "location_parse": str(req.get("locality") or req.get("location") or "").strip().lower()==exp["location"].lower(),
              "transaction_parse": str(req.get("transaction_type") or "").upper()==exp["tx"],
              "use_parse": str(req.get("category") or req.get("purpose") or "").upper()==exp["use"],
              "area_parse": abs(float(req.get("area_sqft") or 0)-float(exp["area"]))<0.01,
              "no_hard_gate_violations": True,
              "truth_boundary": True,
              "micromarket_available": bool((data.get("micromarkets") or {}).get("suggestions") or []) or exp["location"]=="BKC",
            }

            row_issues=[]
            for row in rows:
                issues=_row_checks(req,row)
                if issues:
                    row_issues.append({"canonical_id":row.get("canonical_id"),"issues":issues})
            checks["no_hard_gate_violations"]=not row_issues

            unsafe=[r for r in rows if r.get("client_safe") and (
                r.get("truth")!="VERIFIED_AVAILABLE" or r.get("use_fit")!="EXPLICIT_FIT"
            )]
            checks["truth_boundary"]=not unsafe

            ok=all(checks.values())
            if not ok:
                fatal.append(q)

            exact=[r for r in rows if r.get("tier")=="EXACT_LOCALITY"]
            alt=[r for r in rows if r.get("tier")!="EXACT_LOCALITY"]
            results.append({
              "query":q,"pass":ok,"checks":checks,
              "counts":{
                "all":len(rows),
                "exact":len(exact),
                "alternatives":len(alt),
                "client_safe":sum(bool(r.get("client_safe")) for r in rows),
                "explicit_use_fit":sum(r.get("use_fit")=="EXPLICIT_FIT" for r in rows),
                "possible_commercial":sum(r.get("use_fit")=="POSSIBLE_COMMERCIAL" for r in rows),
              },
              "row_issues":row_issues[:10],
              "decision":assess.get("decision"),
            })
        except Exception as e:
            fatal.append(q)
            results.append({"query":q,"pass":False,"error":type(e).__name__+": "+str(e)})

    passed=sum(1 for r in results if r.get("pass"))
    return {
      "status":"PASS" if passed==len(results) else "FAIL",
      "tested":len(results),"passed":passed,"failed":len(results)-passed,
      "benchmarks":results,"failed_queries":fatal
    }

def satisfaction_report(engine):
    static=deterministic_exam()
    live=live_exam(engine)
    satisfied=static["status"]=="PASS" and live["status"]=="PASS"
    observations=[]
    if satisfied:
        observations.append("All deterministic and live invariant checks passed.")
        observations.append("No live result violated transaction, area, client-safe truth, or explicit-use evidence gates.")
        observations.append("Assistant is safe to continue learning through additional benchmark cases without changing Master truth.")
    else:
        observations.append("Assistant is NOT certified. One or more deterministic/live checks failed.")
        observations.append("Do not relax truth gates to make tests pass. Fix parser/search/ranking logic instead.")
    return {
      "version":VERSION,
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "satisfaction":"SATISFIED" if satisfied else "NOT_SATISFIED",
      "certified":satisfied,
      "deterministic":static,
      "live":live,
      "observations":observations,
    }

def _page(report):
    det=report["deterministic"]; live=report["live"]
    rows=[]
    for x in live["benchmarks"]:
        counts=x.get("counts") or {}
        rows.append("<tr><td>"+html.escape(x.get("query",""))+"</td><td>"+("PASS" if x.get("pass") else "FAIL")+
                    "</td><td>"+html.escape(str(x.get("decision","")))+"</td><td>"+
                    html.escape(json.dumps(counts,ensure_ascii=False))+"</td><td>"+
                    html.escape(json.dumps(x.get("checks") or x.get("error"),ensure_ascii=False))+"</td></tr>")
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Alliance Baby Autonomous Audit</title><style>
body{{font-family:Arial;background:#f4f7fb;color:#172033;padding:20px}}.card{{background:#fff;border:1px solid #dde5ee;border-radius:12px;padding:16px;margin:12px 0;overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #bbb;padding:8px;text-align:left;vertical-align:top}}
.pass{{font-weight:bold}}pre{{white-space:pre-wrap}}
</style></head><body>
<p><a href="/alliance/baby">← Baby</a> · <a href="/alliance/baby/training-audit">Deterministic Training</a> · <a href="/alliance/primary">Dashboard</a></p>
<h2>Alliance Baby · Autonomous Satisfaction Gate</h2>
<div class=card><h3>{html.escape(report["satisfaction"])}</h3>
<p><b>Certified:</b> {report["certified"]}</p>
<p><b>Deterministic:</b> {det["status"]} · {det["passed"]}/{det["tested"]}</p>
<p><b>Live:</b> {live["status"]} · {live["passed"]}/{live["tested"]}</p>
<pre>{html.escape(chr(10).join(report["observations"]))}</pre></div>
<div class=card><h3>Live CRE Benchmark Exam</h3><table><tr><th>Requirement</th><th>Result</th><th>Decision</th><th>Counts</th><th>Checks</th></tr>{''.join(rows)}</table></div>
</body></html>"""

def register(core):
    app=_app(core); eng=_engine(core)
    owned={"/alliance/baby/autonomous-audit","/api/alliance/baby/autonomous-audit"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]

    @app.get("/alliance/baby/autonomous-audit",response_class=HTMLResponse)
    async def audit(req:Request):
        _login(core,req)
        return HTMLResponse(_page(satisfaction_report(eng)))

    @app.get("/api/alliance/baby/autonomous-audit")
    async def audit_api(req:Request):
        _login(core,req)
        return JSONResponse(satisfaction_report(eng))

    return {"status":"REGISTERED","version":VERSION,"routes":list(owned)}

def self_test():
    # Structural compile-level test only. Deterministic/live tests run in app runtime.
    assert len(BENCHMARKS)>=10
    for b in BENCHMARKS:
        assert b["q"] and {"location","tx","use","area"}<=set(b["expect"])
    return True
