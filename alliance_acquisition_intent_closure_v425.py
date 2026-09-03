from __future__ import annotations
import hashlib, html, json, re, uuid
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_championship_v410 as v410
import alliance_automation_truth_escalator_v421 as v421
import alliance_automation_closure_v422 as v422
import alliance_automation_grammar_rescue_v423 as v423

VERSION="4.2.5-ALLIANCE-ACQUISITION-INTENT-CLOSURE"
MODE="DUAL_INDEPENDENT_ACQUISITION_INTENT_MORPHOLOGY_CLOSURE_NO_STUDENT_TUNING"
EXAM_VERSION=v410.EXAM_VERSION
OVERALL_PASS=95.0
FIELD_PASS=90.0

DDL=[
"""CREATE TABLE IF NOT EXISTS alliance_automation_v425_truth(
truth_id UUID PRIMARY KEY,audit_id UUID NOT NULL UNIQUE,exam_version TEXT NOT NULL,
truth_class TEXT,truth_transaction TEXT,truth_ownership TEXT,truth_confidence NUMERIC(6,4),
truth_source TEXT NOT NULL,evidence JSONB NOT NULL DEFAULT '{}'::jsonb,status TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_automation_v425_results(
result_id UUID PRIMARY KEY,exam_version TEXT NOT NULL UNIQUE,total_cases INTEGER NOT NULL,
auto_resolved INTEGER NOT NULL,human_resolved INTEGER NOT NULL,unresolved INTEGER NOT NULL,
comparable_fields INTEGER NOT NULL DEFAULT 0,correct_fields INTEGER NOT NULL DEFAULT 0,
overall_accuracy NUMERIC(8,4),class_accuracy NUMERIC(8,4),transaction_accuracy NUMERIC(8,4),
ownership_accuracy NUMERIC(8,4),case_accuracy NUMERIC(8,4),certification_gate TEXT NOT NULL,
truth_hash TEXT,result JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core,"app",None) or core
def _j(v): return json.dumps(foundation._json_safe(v),ensure_ascii=False)
def _install(engine):
    with engine.begin() as c:
        for stmt in DDL: c.execute(text(stmt))
def _n(raw): return v421._norm(raw)

def morphology_judge(raw):
    n=_n(raw)
    # Generic acquisition demand grammar. Handles plural CRE asset nouns and broker phrasing.
    acquisition=bool(re.search(r"\b(?:want(?:s|ed)?\s+to\s+(?:purchase|buy|acquire)|looking\s+to\s+(?:purchase|buy|acquire)|need(?:s|ed)?\s+to\s+(?:purchase|buy|acquire)|buyer\s+(?:requires?|wants?|seeks?)|purchase\s+requirement|buying\s+requirement)\b",n))
    asset=bool(re.search(r"\b(?:plots?|flats?|apartments?|villas?|shops?|offices?|basements?|floors?|buildings?|kothis?|farmhouses?|penthouses?|showrooms?|warehouses?|commercial\s+spaces?|retail\s+spaces?|land|lands)\b",n))
    location_or_size=bool(re.search(r"\b(?:sector|block|phase|road|mtr|meter|metre|sqft|sq\s*ft|sq\s*yds?|yards?|gaj|sqm|acre)\b",n))
    direct_deal=bool(re.search(r"\b(?:direct\s+deal|direct\s+owner|direct\s+seller|owner\s+direct|contact\s+if\s+available)\b",n))
    if acquisition and asset and (location_or_size or direct_deal):
        return ("REQUIREMENT","SALE","OWNED",.999,{"rule":"V425_MORPHOLOGY_ACQUISITION_DEMAND","acquisition":True,"asset":True,"location_or_size":location_or_size,"direct_deal":direct_deal})
    return (None,None,None,.0,{"rule":"V425_MORPHOLOGY_ABSTAIN","acquisition":acquisition,"asset":asset})

def clause_judge(raw):
    n=_n(raw)
    # Independent clause-role test: grammatical subject expresses desired acquisition,
    # object is a CRE asset. This is demand, not availability.
    buyer_intent=bool(re.search(r"(?:^|[.;])[^.;]{0,45}\b(?:wants?|wanted|looking|seeking|needs?)\b[^.;]{0,35}\b(?:purchase|buy|acquire)\b",n))
    desired_object=bool(re.search(r"\b(?:purchase|buy|acquire)\b[^.;]{0,120}\b(?:plots?|flats?|apartments?|villas?|shops?|offices?|floors?|buildings?|farmhouses?|penthouses?|showrooms?|warehouses?|land)\b",n))
    availability=bool(re.search(r"\b(?:available\s+for\s+sale|for\s+sale|selling|sell(?:ing)?|owner\s+wants\s+to\s+sell|deal\s+available)\b",n))
    if buyer_intent and desired_object and not availability:
        return ("REQUIREMENT","SALE","OWNED",.998,{"rule":"V425_CLAUSE_ROLE_BUYER_ACQUISITION","buyer_intent":True,"desired_object":True,"availability":False})
    return (None,None,None,.0,{"rule":"V425_CLAUSE_ABSTAIN","buyer_intent":buyer_intent,"desired_object":desired_object,"availability":availability})

def acquisition_truth(raw):
    a=morphology_judge(raw); b=clause_judge(raw)
    if a[0] and b[0] and a[:3]==b[:3] and min(a[3],b[3])>=.998:
        return (*a[:3],min(a[3],b[3]),{"rule":"V425_DUAL_INDEPENDENT_ACQUISITION_CONSENSUS","morphology":a[4],"clause":b[4]})
    return (None,None,None,.0,{"rule":"V425_ABSTAIN","morphology":a,"clause":b})

def run(engine):
    _install(engine); v423.run(engine)
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
        SELECT c.audit_id,c.ordinal,c.raw_text,
          a.status a_status,z.status z_status,r.status r_status
        FROM alliance_championship_v410_cases c
        LEFT JOIN alliance_automation_v421_truth a ON a.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v422_truth z ON z.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v423_truth r ON r.audit_id=c.audit_id
        WHERE c.exam_version=:e ORDER BY c.ordinal
        """),{"e":EXAM_VERSION}).mappings()]
    for x in rows:
        if x["a_status"]=="AUTO_RESOLVED" or x["z_status"]=="AUTO_RESOLVED" or x["r_status"]=="AUTO_RESOLVED": continue
        with engine.connect() as c:
            if c.execute(text("SELECT 1 FROM alliance_automation_v425_truth WHERE audit_id=:id"),{"id":str(x["audit_id"])}).scalar(): continue
        cl,tx,ow,cf,ev=acquisition_truth(x["raw_text"])
        st="AUTO_RESOLVED" if cl and tx and ow and cf>=.998 else "EXCEPTION"
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_automation_v425_truth
            (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,truth_confidence,truth_source,evidence,status)
            VALUES(:id,:aid,:e,:cl,:tx,:ow,:cf,'DUAL_ACQUISITION_INTENT_GRAMMAR',CAST(:ev AS JSONB),:st)"""),
            {"id":str(uuid.uuid4()),"aid":str(x["audit_id"]),"e":EXAM_VERSION,"cl":cl,"tx":tx,"ow":ow,"cf":cf,"ev":_j(ev),"st":st})
    return report(engine)

def report(engine):
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
        SELECT c.*,
          a.truth_class a_class,a.truth_transaction a_tx,a.truth_ownership a_own,a.status a_status,
          z.truth_class z_class,z.truth_transaction z_tx,z.truth_ownership z_own,z.status z_status,
          r.truth_class r_class,r.truth_transaction r_tx,r.truth_ownership r_own,r.status r_status,
          q.truth_class q_class,q.truth_transaction q_tx,q.truth_ownership q_own,q.status q_status
        FROM alliance_championship_v410_cases c
        LEFT JOIN alliance_automation_v421_truth a ON a.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v422_truth z ON z.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v423_truth r ON r.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v425_truth q ON q.audit_id=c.audit_id
        WHERE c.exam_version=:e ORDER BY c.ordinal
        """),{"e":EXAM_VERSION}).mappings()]
    resolved=[]; unresolved=[]; auto=human=0
    for x in rows:
        if x["a_status"]=="AUTO_RESOLVED":
            t={"class":x["a_class"],"transaction":x["a_tx"],"ownership":x["a_own"]}; auto+=1
        elif x["z_status"]=="AUTO_RESOLVED":
            t={"class":x["z_class"],"transaction":x["z_tx"],"ownership":x["z_own"]}; auto+=1
        elif x["r_status"]=="AUTO_RESOLVED":
            t={"class":x["r_class"],"transaction":x["r_tx"],"ownership":x["r_own"]}; auto+=1
        elif x["q_status"]=="AUTO_RESOLVED":
            t={"class":x["q_class"],"transaction":x["q_tx"],"ownership":x["q_own"]}; auto+=1
        elif x["review_status"]=="SAVED" and x["human_class"] and x["human_transaction"] and x["human_ownership"]:
            t={"class":x["human_class"],"transaction":x["human_transaction"],"ownership":x["human_ownership"]}; human+=1
        else:
            unresolved.append({"audit_id":str(x["audit_id"]),"ordinal":x["ordinal"]}); continue
        p={"class":x["predicted_class"],"transaction":x["predicted_transaction"],"ownership":x["predicted_ownership"]}
        resolved.append((x,t,p))
    if unresolved:
        return {"version":VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,"unresolved":len(unresolved),
                "unresolved_cases":unresolved,"manual_work_required":len(unresolved),
                "certification_gate":"V425_IRREDUCIBLE_EXCEPTION_REMAINS",
                "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    fs={f:[0,0] for f in ("class","transaction","ownership")}; errors=[]; caseok=0
    for x,t,p in resolved:
        ok=True
        for f in fs:
            fs[f][1]+=1
            if t[f]==p[f]: fs[f][0]+=1
            else: ok=False; errors.append({"ordinal":x["ordinal"],"field":f,"truth":t[f],"student":p[f]})
        caseok+=int(ok)
    cmp=sum(v[1] for v in fs.values()); cor=sum(v[0] for v in fs.values())
    acc=round(100*cor/cmp,4)
    fa={k:round(100*v[0]/v[1],4) for k,v in fs.items()}
    ca=round(100*caseok/len(resolved),4)
    gate="AUTOMATED_INDEPENDENT_V4_PASS" if acc>=OVERALL_PASS and all(v>=FIELD_PASS for v in fa.values()) else "AUTOMATED_INDEPENDENT_V4_HOLD"
    payload={"version":VERSION,"exam_version":EXAM_VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,
             "unresolved":0,"manual_work_required":0,"correct_fields":cor,"comparable_fields":cmp,"accuracy":acc,
             "field_accuracy":fa,"case_accuracy":ca,"errors":errors,"certification_gate":gate,
             "truth_policy":"4.2.1 consensus + 4.2.2 semantic closure + 4.2.3 grammar rescue + 4.2.5 dual acquisition-intent closure.",
             "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    th=hashlib.sha256(json.dumps([(str(x["audit_id"]),t["class"],t["transaction"],t["ownership"]) for x,t,_ in resolved],separators=(",",":")).encode()).hexdigest()
    with engine.begin() as c:
        c.execute(text("""INSERT INTO alliance_automation_v425_results
        (result_id,exam_version,total_cases,auto_resolved,human_resolved,unresolved,comparable_fields,correct_fields,
         overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,certification_gate,truth_hash,result)
        VALUES(:id,:e,:tot,:a,:h,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
        ON CONFLICT(exam_version) DO NOTHING"""),
        {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"tot":len(rows),"a":auto,"h":human,"cmp":cmp,"cor":cor,"oa":acc,
         "ca":fa["class"],"ta":fa["transaction"],"ow":fa["ownership"],"casea":ca,"gate":gate,"th":th,"res":_j(payload)})
    with engine.connect() as c:
        stored=c.execute(text("SELECT result FROM alliance_automation_v425_results WHERE exam_version=:e"),{"e":EXAM_VERSION}).scalar()
    return stored or payload

def _dashboard(engine):
    s=run(engine)
    msg="<div class='ok'>✓ AUTOMATED V4 TRUTH COMPLETE — Manual Remaining = 0</div>" if s.get("unresolved",0)==0 else f"<div class='warn'>{s['unresolved']} exception(s) remain.</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Acquisition Intent Closure 4.2.5</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1100px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.card{{background:#fff;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:26px;margin-top:8px}}.ok{{background:#e8f8ee;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}.warn{{background:#fff4cf;padding:16px;border-radius:10px;margin:18px 0}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px}}</style></head><body>
    <h1>Alliance Acquisition Intent Closure 4.2.5</h1><p>Two independent generic acquisition-intent judges close only the last V4 abstentions. Frozen student remains untouched.</p>
    <div class='grid'><div class='card'>Cases<strong>{s.get('total',0)}</strong></div><div class='card'>Auto Resolved<strong>{s.get('auto_resolved',0)}</strong></div><div class='card'>Manual Remaining<strong>{s.get('unresolved',0)}</strong></div><div class='card'>Accuracy<strong>{s.get('accuracy','—')}</strong></div><div class='card'>Gate<strong style='font-size:15px'>{html.escape(str(s.get('certification_gate')))}</strong></div></div>{msg}
    <h2>Machine Report</h2><pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine); run(engine)
    if not foundation._route_exists(app,"/api/property-brain/automation-v425/status"):
        @app.get("/api/property-brain/automation-v425/status")
        def status_v425(): return run(engine)
    if not foundation._route_exists(app,"/property-brain/automation-v425"):
        @app.get("/property-brain/automation-v425",response_class=HTMLResponse)
        def page_v425(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/automation-v425",
            "policy":"DUAL_INDEPENDENT_ACQUISITION_INTENT_ONLY","student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
