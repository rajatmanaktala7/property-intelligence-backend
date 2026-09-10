from __future__ import annotations
import json,re,html
from datetime import datetime,timezone
from sqlalchemy import inspect,text
from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse

VERSION="4.1.0-ALLIANCE-BABY-AUTOMATIC-MARKET-EVIDENCE"

TABLE_HINTS=("commercial","hospitality","retail","restaurant","discovery","marketing","brand","mall")
NAME_COLS=("brand_name","restaurant_name","business_name","entity_name","name","title","company_name","retailer_name","property_name")
LOCATION_COLS=("location","locality","market","area","micro_market","preferred_location","address")
CATEGORY_COLS=("category","business_type","entity_type","property_type","suitable_category","segment","vertical","use_type","remarks","description")
SOURCE_COLS=("source_url","source_reference","source","url","website","linkedin_url","google_maps_url")
DATE_COLS=("updated_at","created_at","discovered_at","verified_at","date","published_at")

def _n(v): return re.sub(r"\s+"," ",str(v or "").strip()).upper()
def _clean_name(v):
    s=_n(v)
    s=re.sub(r"[^A-Z0-9& ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def _first(cols,choices):
    low={c.lower():c for c in cols}
    for x in choices:
        if x in low:return low[x]
    return None
def _loc_match(value,target):
    a=_n(value);b=_n(target)
    return bool(a and b and (a==b or b in a or a in b))
def _kind(req):
    import alliance_baby_use_case_market_intelligence_v4 as v4
    return v4.category(req)

def _keywords(kind):
    return {
      "FNB":("RESTAURANT","CAFE","CAFÉ","F&B","FNB","QSR","FOOD","BAR","LOUNGE","KITCHEN"),
      "FASHION":("FASHION","GARMENT","APPAREL","CLOTHING","CLOTHES","FOOTWEAR"),
      "JEWELLERY":("JEWELLERY","JEWELRY","GOLD","DIAMOND"),
      "LUXURY":("LUXURY","PREMIUM","DESIGNER"),
      "GROCERY":("GROCERY","SUPERMARKET","CONVENIENCE","DAILY NEED"),
      "OFFICE":("OFFICE","CORPORATE","WORKSPACE","BUSINESS CENTRE","BUSINESS CENTER"),
      "RETAIL":("RETAIL","SHOP","SHOWROOM","STORE","BRAND"),
    }.get(kind,("RETAIL",))

def _row_relevant(row,kind,cat_col,name_col):
    hay=" ".join(str(row.get(k) or "") for k in row)
    if kind=="FNB":
        return any(k in _n(hay) for k in _keywords(kind))
    if kind in ("FASHION","JEWELLERY","LUXURY","GROCERY","OFFICE"):
        if cat_col and any(k in _n(row.get(cat_col)) for k in _keywords(kind)): return True
        # Without category/use evidence, do not infer category from a generic business name.
        return False
    return True

def discover_sources(engine):
    """Read-only schema discovery. No table creation or mutation."""
    out=[]
    try:
        ins=inspect(engine)
        for table in ins.get_table_names():
            tl=table.lower()
            if not any(h in tl for h in TABLE_HINTS): continue
            cols=[c["name"] for c in ins.get_columns(table)]
            name=_first(cols,NAME_COLS);loc=_first(cols,LOCATION_COLS)
            if not name or not loc: continue
            out.append({
              "table":table,"name_col":name,"location_col":loc,
              "category_col":_first(cols,CATEGORY_COLS),
              "source_col":_first(cols,SOURCE_COLS),
              "date_col":_first(cols,DATE_COLS),
            })
    except Exception as e:
        return {"status":"ERROR","error":f"{type(e).__name__}: {e}","sources":[]}
    return {"status":"OK","sources":out}

def _safe_ident(s):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(s or "")): raise ValueError("unsafe SQL identifier")
    return s

def collect(engine,req,target,limit_per_table=750):
    kind=_kind(req); src=discover_sources(engine)
    identities={}; provenance=[]; source_errors=[]
    if src["status"]!="OK":
        return {"status":"UNKNOWN","kind":kind,"market":target,"distinct_count":0,"entities":[],"provenance":[],"errors":[src.get("error")]}
    for meta in src["sources"]:
        try:
            table=_safe_ident(meta["table"])
            cols=[meta["name_col"],meta["location_col"]]
            for x in ("category_col","source_col","date_col"):
                if meta.get(x) and meta[x] not in cols: cols.append(meta[x])
            sel=",".join('"'+_safe_ident(c)+'"' for c in cols)
            sql=text(f'SELECT {sel} FROM "{table}" LIMIT :lim')
            with engine.connect() as c:
                rows=[dict(r._mapping) for r in c.execute(sql,{"lim":int(limit_per_table)})]
            for row in rows:
                if not _loc_match(row.get(meta["location_col"]),target): continue
                if not _row_relevant(row,kind,meta.get("category_col"),meta["name_col"]): continue
                nm=str(row.get(meta["name_col"]) or "").strip(); key=_clean_name(nm)
                if not key: continue
                source=str(row.get(meta["source_col"]) or "") if meta.get("source_col") else ""
                dt=str(row.get(meta["date_col"]) or "") if meta.get("date_col") else ""
                ent=identities.setdefault(key,{"name":nm,"sources":[]})
                proof={"table":table,"source":source,"date":dt}
                if proof not in ent["sources"]: ent["sources"].append(proof)
                provenance.append({"entity":nm,**proof})
        except Exception as e:
            source_errors.append({"table":meta["table"],"error":f"{type(e).__name__}: {e}"})

    entities=sorted(identities.values(),key=lambda x:_n(x["name"]))
    count=len(entities)
    threshold=4 if kind=="FNB" else 3 if kind in ("FASHION","JEWELLERY","LUXURY") else 1
    status="STRONG" if count>=threshold else "INSUFFICIENT" if count>0 else "UNKNOWN"
    return {"status":status,"kind":kind,"market":target,"distinct_count":count,"threshold":threshold,
            "entities":entities[:100],"provenance":provenance[:200],"source_tables":[x["table"] for x in src["sources"]],
            "errors":source_errors,"truth_rule":"Evidence supports market suitability only. It never proves property availability or verification."}

def evidence_for_v4(engine,req,target):
    ev=collect(engine,req,target)
    kind=ev["kind"]
    payload={"source_urls":[p["source"] for p in ev["provenance"] if p.get("source")]}
    if kind=="FNB": payload["restaurant_count"]=ev["distinct_count"]
    if kind in ("FASHION","JEWELLERY","LUXURY","RETAIL"): payload["relevant_brand_count"]=ev["distinct_count"]
    return ev,payload

def assess_market(engine,req,target):
    import alliance_baby_use_case_market_intelligence_v4 as v4
    evidence,payload=evidence_for_v4(engine,req,target)
    decision=v4.classify(req,target,payload)
    return {"market":target,"evidence":evidence,"v4_decision":decision,
            "client_safe_property":False,
            "next_action":"SEARCH_MASTER" if decision.get("recommendable") else "SEARCH_MORE_EVIDENCE",
            "truth_boundary":"Market recommendation is not a property recommendation."}

def candidate_markets(req):
    import alliance_baby_use_case_market_intelligence_v4 as v4
    import alliance_micromarket_knowledge_v1 as g
    origin=req.get("locality") or req.get("location") or "";kind=v4.category(req)
    seen=[];om=g.market(origin)
    def add(x):
        if x and _n(x)!=_n(origin) and _n(x) not in {_n(y) for y in seen}: seen.append(x)
    if kind in ("FASHION","JEWELLERY","LUXURY"):
        pool=v4.FASHION if kind=="FASHION" else v4.JEWELLERY if kind=="JEWELLERY" else v4.LUXURY
        for x in sorted(pool): add(next((m[1] for m in g.MARKETS if _n(m[1])==_n(x)),x.title()))
    elif kind in ("GROCERY","OFFICE"):
        for m in g.MARKETS:
            d=g.km(om,m) if om and m[1]!=om[1] and m[0]==om[0] else None
            if d is not None and d<=3: add(m[1])
    else:
        if om:
            for x in om[7] or []: add(x)
            for m in g.MARKETS:
                if m[0]==om[0] and m[1]!=om[1] and m[3] in v4.FNB_TYPES: add(m[1])
    return seen[:20]

def plan(engine,req):
    markets=candidate_markets(req)
    cards=[assess_market(engine,req,m) for m in markets]
    cards.sort(key=lambda x:(bool(x["v4_decision"].get("recommendable")),x["v4_decision"].get("score",0),x["evidence"].get("distinct_count",0)),reverse=True)
    return {"requirement":req,"category":_kind(req),"markets":cards,
            "recommended_markets":[x for x in cards if x["v4_decision"].get("recommendable")],
            "search_targets":[x for x in cards if not x["v4_decision"].get("recommendable")]}

def synthetic_exam():
    class FakeResult:
        def __init__(self,rows): self.rows=rows
        def __iter__(self):
            class R:
                def __init__(self,d): self._mapping=d
            return iter([R(x) for x in self.rows])
    # Pure algorithm invariants independent of live schema are checked directly.
    tests=[]
    def add(n,ok): tests.append({"name":n,"pass":bool(ok)})
    add("dedup normalization",_clean_name(" Cafe  Delhi! ")==_clean_name("CAFE DELHI"))
    add("location exact",_loc_match("Saket","saket"))
    add("location embedded",_loc_match("Saket, New Delhi","Saket"))
    add("location mismatch",not _loc_match("Saket","Bandra West"))
    add("safe identifier",_safe_ident("aci_intel_results")=="aci_intel_results")
    try:_safe_ident("x;drop table y"); safe=False
    except ValueError:safe=True
    add("unsafe identifier blocked",safe)
    # V4 evidence thresholds.
    import alliance_baby_use_case_market_intelligence_v4 as v4
    import alliance_baby_cre_copilot_v1 as baby
    req=baby._parse_free_text("Need restaurant in Saket")
    add("3 restaurants insufficient",v4.classify(req,"Karol Bagh",{"restaurant_count":3})["class"]=="SEARCH_TARGET")
    add("4 restaurants qualifies",v4.classify(req,"Karol Bagh",{"restaurant_count":4})["class"]=="USE_CASE_COMPARABLE")
    req2=baby._parse_free_text("Need garment store in Saket")
    add("3 relevant brands can qualify",v4.classify(req2,"Hauz Khas",{"relevant_brand_count":3})["class"]=="USE_CASE_COMPARABLE")
    passed=sum(x["pass"] for x in tests)
    return {"status":"PASS" if passed==len(tests) else "FAIL","tested":len(tests),"passed":passed,"failed":len(tests)-passed,"tests":tests}

def live_audit(engine):
    src=discover_sources(engine)
    cases=[
      ("Need restaurant in Saket","Hauz Khas"),
      ("Need restaurant in Saket","Defence Colony"),
      ("Need garment store in Saket","Lajpat Nagar"),
      ("Need garment store in Saket","Rajouri Garden"),
    ]
    import alliance_baby_cre_copilot_v1 as baby
    out=[]
    for q,m in cases:
        try:
            req=baby._parse_free_text(q);a=assess_market(engine,req,m)
            out.append({"query":q,"market":m,"evidence_status":a["evidence"]["status"],
                        "count":a["evidence"]["distinct_count"],"decision":a["v4_decision"]["class"],
                        "source_tables":a["evidence"]["source_tables"],"errors":a["evidence"]["errors"]})
        except Exception as e:
            out.append({"query":q,"market":m,"evidence_status":"ERROR","error":f"{type(e).__name__}: {e}"})
    # Live audit is informational when database has no compatible evidence tables.
    return {"version":VERSION,"schema_discovery":src,"cases":out,
            "note":"UNKNOWN means no qualifying internal evidence. It must not be converted into invented counts."}

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _page(report):
    rows=[]
    for x in report["cases"]:
        rows.append("<tr><td>"+html.escape(x.get("query",""))+"</td><td>"+html.escape(x.get("market",""))+"</td><td>"+html.escape(str(x.get("evidence_status","")))+"</td><td>"+html.escape(str(x.get("count","")))+"</td><td>"+html.escape(str(x.get("decision","")))+"</td><td>"+html.escape(", ".join(x.get("source_tables") or []))+"</td></tr>")
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Baby V4.1 Market Evidence Audit</title>
<style>body{{font-family:Arial;padding:20px;background:#f4f7fb}}.card{{background:white;padding:16px;border-radius:12px;margin:12px 0}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:8px;text-align:left}}</style></head><body>
<h2>Alliance Baby V4.1 · Automatic Market Evidence</h2><div class=card><b>Schema discovery:</b> {html.escape(str(report["schema_discovery"].get("status")))}<br>{html.escape(report["note"])}</div>
<div class=card><table><tr><th>Requirement</th><th>Market</th><th>Evidence</th><th>Count</th><th>V4 Decision</th><th>Internal Sources</th></tr>{''.join(rows)}</table></div></body></html>"""

def register(core):
    app=_app(core);eng=_engine(core)
    owned={"/alliance/baby/v4-market-evidence-audit","/api/alliance/baby/v4-market-evidence-audit"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/baby/v4-market-evidence-audit",response_class=HTMLResponse)
    async def page(req:Request): return HTMLResponse(_page(live_audit(eng)))
    @app.get("/api/alliance/baby/v4-market-evidence-audit")
    async def api(req:Request): return JSONResponse(live_audit(eng))
    return {"status":"REGISTERED","version":VERSION,"routes":sorted(owned),"exam":synthetic_exam()}
