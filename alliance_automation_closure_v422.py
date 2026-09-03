from __future__ import annotations

import hashlib, html, json, re, uuid
from collections import defaultdict
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_championship_v410 as v410
import alliance_automation_truth_escalator_v421 as v421

VERSION="4.2.2-ALLIANCE-AUTOMATION-CLOSURE"
MODE="DETERMINISTIC_CRE_SEMANTIC_CLOSURE_AUTO_ONLY_NO_STUDENT_TUNING"
EXAM_VERSION=v410.EXAM_VERSION
OVERALL_PASS=95.0
FIELD_PASS=90.0

DDL=[
"""CREATE TABLE IF NOT EXISTS alliance_automation_v422_truth(
truth_id UUID PRIMARY KEY,audit_id UUID NOT NULL UNIQUE,exam_version TEXT NOT NULL,
truth_class TEXT,truth_transaction TEXT,truth_ownership TEXT,
truth_confidence NUMERIC(6,4),truth_source TEXT NOT NULL,evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
status TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_automation_v422_results(
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
        for x in DDL:c.execute(text(x))
def _n(raw): return v421._norm(raw)
def _s(raw): return v421._signals(raw)
def _g(raw): return v421._group_strength(raw)

def semantic_truth(raw):
    n=_n(raw); s=_s(raw); group,ge=_g(raw)
    ev={"rule":"V422_CRE_SEMANTIC_CLOSURE","signals":s,"group":ge}

    # Pure greetings/admin/standalone links are deterministic NOISE.
    greeting=bool(re.search(r"\b(?:good morning|good evening|good night|best wishes|happy birthday|raksha bandhan|rakshabandhan|congratulations)\b|शुभकामनाएं",n))
    admin=bool(re.search(r"\b(?:this group|group for rented properties|request everyone|remove such content|please don'?t post)\b",n))
    url_only=bool(re.fullmatch(r"(?:url\s*){1,6}",n))
    if (greeting or admin or url_only) and not (s["property"] or s["req"] or s["sale"] or s["avail"]):
        return ("NOISE","UNKNOWN","NOT_OWNED",.999,ev)

    # Requirements: "required/wanted/need" is demand even when property specifications are detailed.
    req=bool(re.search(r"\b(?:immediate(?:ly)?\s+required|required|requirement|wanted|need(?:ed)?|seeking|buyer required|client wants?)\b",n))
    availability=bool(re.search(r"\b(?:available|avl|for sale|for rent|available for lease|available on lease|deal available|get(?:ting)? vacated|showing|for showing|to let)\b",n))
    if req and not availability:
        rent_req=bool(re.search(r"\b(?:rent|rental|lease|leasing|tenant)\b",n))
        sale_req=bool(re.search(r"\b(?:buy|purchase|buyer|outright|acquisition)\b",n) or
                      re.search(r"\bbudget\b.{0,45}(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh)",n))
        tx="RENT" if rent_req else ("SALE" if sale_req or s["capital"] else "UNKNOWN")
        return ("REQUIREMENT",tx,"OWNED",.999 if tx!="UNKNOWN" else .985,ev)

    # Explicit multi-option / portfolio / mixed parent.
    if group:
        if ge["mixed"]: tx="AMBIGUOUS"
        elif s["tenancy"] and s["capital"]: tx="SALE"
        elif s["sale"] or s["capital"]: tx="SALE"
        elif s["rent"] or s["monthly"]: tx="RENT"
        else: tx="UNKNOWN"
        return ("INVENTORY_GROUP",tx,"OWNED",.998 if tx!="UNKNOWN" else .985,ev)

    # Investment property: rent/tenant describes occupancy; capital consideration defines SALE.
    if s["tenancy"] and s["capital"]:
        return ("PROPERTY_AVAILABILITY","SALE","OWNED",.999,ev)

    # Availability with capital value. Avoid interpreting monthly rent as capital.
    if s["property"] and availability:
        explicit_sale=bool(re.search(r"\b(?:for sale|available for sale|outright|resale|asking price|owner wants|sale inventory)\b",n))
        explicit_rent=bool(re.search(r"\b(?:for rent|available for rent|available for lease|available on lease|to let|getting vacated|rent upto|rent up to|asking rent|showing rent)\b",n))
        if explicit_sale and explicit_rent: tx="AMBIGUOUS"
        elif explicit_sale: tx="SALE"
        elif explicit_rent: tx="RENT"
        elif s["capital"] and not s["monthly"]: tx="SALE"
        elif s["monthly"]: tx="RENT"
        else: tx="UNKNOWN"
        return ("PROPERTY_AVAILABILITY",tx,"OWNED",.998 if tx!="UNKNOWN" else .982,ev)

    # Property + clear economics even when "available" omitted.
    if s["property"] and (s["sale"] or s["rent"] or s["capital"] or s["monthly"]):
        if s["tenancy"] and s["capital"]: tx="SALE"
        elif s["sale"]: tx="SALE"
        elif s["rent"] or s["monthly"]: tx="RENT"
        elif s["capital"]: tx="SALE"
        else: tx="UNKNOWN"
        return ("PROPERTY_AVAILABILITY",tx,"OWNED",.997 if tx!="UNKNOWN" else .98,ev)

    return (None,None,None,.0,{"rule":"V422_ABSTAIN","signals":s})

def close(engine):
    _install(engine); v421.adjudicate(engine)
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""SELECT c.audit_id,c.ordinal,c.raw_text,
          c.review_status,c.human_class,c.human_transaction,c.human_ownership,
          t.truth_class,t.truth_transaction,t.truth_ownership,t.status auto_status
          FROM alliance_championship_v410_cases c
          LEFT JOIN alliance_automation_v421_truth t ON t.audit_id=c.audit_id
          WHERE c.exam_version=:e ORDER BY c.ordinal"""),{"e":EXAM_VERSION}).mappings()]
    for r in rows:
        # Preserve 4.2.1 auto truth. Closure acts only on unresolved cases.
        if r["auto_status"]=="AUTO_RESOLVED": continue
        with engine.connect() as c:
            if c.execute(text("SELECT 1 FROM alliance_automation_v422_truth WHERE audit_id=:id"),{"id":str(r["audit_id"])}).scalar(): continue
        cls,tx,own,cf,ev=semantic_truth(r["raw_text"])
        status="AUTO_RESOLVED" if cls and tx and own and cf>=.98 else "EXCEPTION"
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_automation_v422_truth
              (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,truth_confidence,truth_source,evidence,status)
              VALUES(:id,:aid,:e,:cl,:tx,:ow,:cf,'DETERMINISTIC_CRE_SEMANTIC_CLOSURE',CAST(:ev AS JSONB),:st)"""),
              {"id":str(uuid.uuid4()),"aid":str(r["audit_id"]),"e":EXAM_VERSION,"cl":cls,"tx":tx,"ow":own,"cf":cf,"ev":_j(ev),"st":status})
    return report(engine)

def report(engine):
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""SELECT c.*,
          a.truth_class a_class,a.truth_transaction a_tx,a.truth_ownership a_own,a.status a_status,
          z.truth_class z_class,z.truth_transaction z_tx,z.truth_ownership z_own,z.status z_status
          FROM alliance_championship_v410_cases c
          LEFT JOIN alliance_automation_v421_truth a ON a.audit_id=c.audit_id
          LEFT JOIN alliance_automation_v422_truth z ON z.audit_id=c.audit_id
          WHERE c.exam_version=:e ORDER BY c.ordinal"""),{"e":EXAM_VERSION}).mappings()]
    resolved=[]; unresolved=[]; auto=human=0
    for r in rows:
        if r["a_status"]=="AUTO_RESOLVED":
            t={"class":r["a_class"],"transaction":r["a_tx"],"ownership":r["a_own"]};auto+=1
        elif r["z_status"]=="AUTO_RESOLVED":
            t={"class":r["z_class"],"transaction":r["z_tx"],"ownership":r["z_own"]};auto+=1
        elif r["review_status"]=="SAVED" and r["human_class"] and r["human_transaction"] and r["human_ownership"]:
            t={"class":r["human_class"],"transaction":r["human_transaction"],"ownership":r["human_ownership"]};human+=1
        else:
            unresolved.append({"audit_id":str(r["audit_id"]),"ordinal":r["ordinal"]});continue
        p={"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        resolved.append((r,t,p))
    if unresolved:
        return {"version":VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,"unresolved":len(unresolved),
                "unresolved_cases":unresolved,"manual_work_required":len(unresolved),
                "certification_gate":"V422_ONLY_TRUE_SEMANTIC_ABSTENTIONS_REMAIN",
                "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    fs={f:[0,0] for f in ("class","transaction","ownership")};errors=[];caseok=0
    for r,t,p in resolved:
        ok=True
        for f in fs:
            fs[f][1]+=1
            if t[f]==p[f]:fs[f][0]+=1
            else:ok=False;errors.append({"ordinal":r["ordinal"],"field":f,"truth":t[f],"student":p[f]})
        caseok+=int(ok)
    cmp=sum(v[1] for v in fs.values());cor=sum(v[0] for v in fs.values())
    acc=round(100*cor/cmp,4);fa={k:round(100*v[0]/v[1],4) for k,v in fs.items()};ca=round(100*caseok/len(resolved),4)
    gate="AUTOMATED_INDEPENDENT_V4_PASS" if acc>=OVERALL_PASS and all(x>=FIELD_PASS for x in fa.values()) else "AUTOMATED_INDEPENDENT_V4_HOLD"
    payload={"version":VERSION,"exam_version":EXAM_VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,"unresolved":0,
             "manual_work_required":0,"correct_fields":cor,"comparable_fields":cmp,"accuracy":acc,"field_accuracy":fa,
             "case_accuracy":ca,"errors":errors,"certification_gate":gate,
             "truth_policy":"4.2.1 independent consensus plus deterministic CRE semantic closure only for its abstentions.",
             "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    th=hashlib.sha256(json.dumps([(str(r["audit_id"]),t["class"],t["transaction"],t["ownership"]) for r,t,_ in resolved],separators=(",",":")).encode()).hexdigest()
    with engine.begin() as c:
        c.execute(text("""INSERT INTO alliance_automation_v422_results
          (result_id,exam_version,total_cases,auto_resolved,human_resolved,unresolved,comparable_fields,correct_fields,
          overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,certification_gate,truth_hash,result)
          VALUES(:id,:e,:tot,:a,:h,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
          ON CONFLICT(exam_version) DO NOTHING"""),
          {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"tot":len(rows),"a":auto,"h":human,"cmp":cmp,"cor":cor,"oa":acc,
           "ca":fa["class"],"ta":fa["transaction"],"ow":fa["ownership"],"casea":ca,"gate":gate,"th":th,"res":_j(payload)})
    with engine.connect() as c:
        stored=c.execute(text("SELECT result FROM alliance_automation_v422_results WHERE exam_version=:e"),{"e":EXAM_VERSION}).scalar()
    return stored or payload

def _dashboard(engine):
    s=close(engine)
    if s.get("unresolved",0)==0:
        msg="<div class='ok'>✓ AUTOMATION COMPLETE — Manual Remaining = 0</div>"
    else:
        msg=f"<div class='warn'>Automation still abstains on {s['unresolved']} case(s). No broad manual review requested.</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Automation Closure 4.2.2</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1100px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.card{{background:white;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:27px;margin-top:8px}}.ok{{background:#e8f8ee;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}.warn{{background:#fff4cf;padding:16px;border-radius:10px;margin:18px 0}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px}}</style></head><body>
    <h1>Alliance Automation Closure 4.2.2</h1><p>Deterministic CRE semantic closure for 4.2.1 abstentions. Frozen student predictions remain untouched.</p>
    <div class='grid'><div class='card'>Cases<strong>{s.get('total',0)}</strong></div><div class='card'>Auto Resolved<strong>{s.get('auto_resolved',0)}</strong></div><div class='card'>Manual Remaining<strong>{s.get('unresolved',0)}</strong></div><div class='card'>Accuracy<strong>{s.get('accuracy','—')}</strong></div><div class='card'>Gate<strong style='font-size:15px'>{html.escape(str(s.get('certification_gate')))}</strong></div></div>{msg}
    <h2>Machine Report</h2><pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core);app=_app(core);_install(engine);close(engine)
    if not foundation._route_exists(app,"/api/property-brain/automation-v422/status"):
        @app.get("/api/property-brain/automation-v422/status")
        def status_v422():return close(engine)
    if not foundation._route_exists(app,"/property-brain/automation-v422"):
        @app.get("/property-brain/automation-v422",response_class=HTMLResponse)
        def page_v422():return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/automation-v422",
            "policy":"AUTOMATION_FIRST_SEMANTIC_CLOSURE","student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
