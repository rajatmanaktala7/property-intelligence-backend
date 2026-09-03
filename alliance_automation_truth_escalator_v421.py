from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
from collections import Counter, defaultdict

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_championship_v410 as v410
import alliance_automation_machine_v420 as v420

VERSION = "4.2.1-ALLIANCE-AUTOMATION-TRUTH-ESCALATOR"
MODE = "FIELDWISE_MULTI_JUDGE_CALIBRATED_HUMAN_PRECEDENT_EXCEPTION_ONLY_NO_STUDENT_TUNING"
EXAM_VERSION = v410.EXAM_VERSION
OVERALL_PASS = 95.0
FIELD_PASS = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_automation_v421_judgments(
judgment_id UUID PRIMARY KEY,
audit_id UUID NOT NULL,
exam_version TEXT NOT NULL,
judge_name TEXT NOT NULL,
predicted_class TEXT,
predicted_transaction TEXT,
predicted_ownership TEXT,
class_confidence NUMERIC(6,4),
transaction_confidence NUMERIC(6,4),
ownership_confidence NUMERIC(6,4),
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
judge_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(audit_id,judge_name,judge_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_automation_v421_truth(
truth_id UUID PRIMARY KEY,
audit_id UUID NOT NULL UNIQUE,
exam_version TEXT NOT NULL,
truth_class TEXT,
truth_transaction TEXT,
truth_ownership TEXT,
class_confidence NUMERIC(6,4),
transaction_confidence NUMERIC(6,4),
ownership_confidence NUMERIC(6,4),
truth_source TEXT NOT NULL,
consensus JSONB NOT NULL DEFAULT '{}'::jsonb,
status TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_automation_v421_results(
result_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
total_cases INTEGER NOT NULL,
auto_resolved INTEGER NOT NULL,
human_resolved INTEGER NOT NULL,
unresolved INTEGER NOT NULL,
comparable_fields INTEGER NOT NULL DEFAULT 0,
correct_fields INTEGER NOT NULL DEFAULT 0,
overall_accuracy NUMERIC(8,4),
class_accuracy NUMERIC(8,4),
transaction_accuracy NUMERIC(8,4),
ownership_accuracy NUMERIC(8,4),
case_accuracy NUMERIC(8,4),
certification_gate TEXT NOT NULL,
truth_hash TEXT,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core,"app",None) or core
def _j(v): return json.dumps(foundation._json_safe(v),ensure_ascii=False)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL: conn.execute(text(stmt))

def _norm(raw): return v420._norm(raw)
def _signals(raw): return v420._signals(raw)
def _group_strength(raw): return v420._group_strength(raw)

def _table_exists(conn,t):
    return bool(conn.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.tables
      WHERE table_schema=current_schema() AND table_name=:t)"""),{"t":t}).scalar())

def _cols(conn,t):
    return set(conn.execute(text("""SELECT column_name FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name=:t"""),{"t":t}).scalars().all())

# ---------------- Judge D: CRE decision graph ----------------
def judge_cre_graph(raw):
    n=_norm(raw); s=_signals(raw); group,ge=_group_strength(raw)
    ev={"rule":"D_CRE_DECISION_GRAPH","signals":s,"group":ge}

    # Noise is a positive semantic decision, not a fallback.
    if re.search(r"\b(?:good morning|good evening|good night|best wishes|happy birthday|raksha bandhan|rakshabandhan)\b|शुभकामनाएं",n):
        if not (s["property"] or s["sale"] or s["rent"] or s["req"]):
            return ("NOISE","UNKNOWN","NOT_OWNED",.997,.999,.999,ev)
    if re.search(r"\b(?:this group|group for rented properties|request everyone|remove such content|please don'?t post)\b",n):
        if not (s["avail"] or s["sale"] or s["req"]):
            return ("NOISE","UNKNOWN","NOT_OWNED",.997,.999,.999,ev)

    # Demand/requirement intent outranks asset nouns. A requirement commonly contains
    # plot/BHK/area/project specifications, so property nouns do not make it availability.
    strong_req=bool(re.search(r"\b(?:immediate(?:ly)? required|required|requirement|wanted|client wants?|buyer required|need(?:ed)?|seeking)\b",n))
    acquisition=bool(re.search(r"\b(?:buy|purchase|buyer|outright)\b",n) or
                     re.search(r"\bbudget\b.{0,30}(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh)",n))
    rental_req=bool(re.search(r"\b(?:required|requirement|wanted|need(?:ed)?)\b.{0,80}\b(?:rent|lease)\b",n) or
                    re.search(r"\b(?:rent|lease)\b.{0,80}\b(?:required|requirement|wanted)\b",n))
    if strong_req and not s["avail"]:
        tx="RENT" if rental_req else ("SALE" if acquisition or s["capital"] else "UNKNOWN")
        return ("REQUIREMENT",tx,"OWNED",.998,.997 if tx!="UNKNOWN" else .965,.999,ev)

    if group:
        tx="AMBIGUOUS" if ge["mixed"] else ("SALE" if s["sale"] or s["capital"] else ("RENT" if s["rent"] or s["monthly"] else "UNKNOWN"))
        return ("INVENTORY_GROUP",tx,"OWNED",.996,.994 if tx!="UNKNOWN" else .96,.999,ev)

    # Pre-leased economics = sale-side investment asset when a capital consideration exists.
    if s["tenancy"] and s["capital"]:
        return ("PROPERTY_AVAILABILITY","SALE","OWNED",.997,.999,.999,ev)

    # Normal availability.
    if s["property"] and (s["avail"] or s["sale"] or s["rent"] or s["capital"] or s["monthly"]):
        tx="SALE" if (s["sale"] or (s["capital"] and not s["rent"])) else ("RENT" if s["rent"] or s["monthly"] else "UNKNOWN")
        return ("PROPERTY_AVAILABILITY",tx,"OWNED",.994,.995 if tx!="UNKNOWN" else .96,.999,ev)

    return (None,None,None,.55,.55,.55,{"rule":"D_ABSTAIN","signals":s})

# ---------------- Judge E: calibrated immutable human precedent ----------------
def _tokens(s):
    n=_norm(s)
    stop={"for","the","and","with","from","this","that","are","is","to","of","in","on","at","a","an","please","call","contact","regards"}
    return {x for x in re.findall(r"[a-z\u0900-\u097f]{3,}|\d+(?:\.\d+)?",n) if x not in stop}

def _human_precedents(engine):
    out=[]
    with engine.connect() as conn:
        tables=conn.execute(text("""SELECT table_name FROM information_schema.tables
          WHERE table_schema=current_schema()""")).scalars().all()
        # Gold spans: immutable human truth.
        if "alliance_gold_span_labels" in tables and "alliance_gold_spans" in tables:
            try:
                for r in conn.execute(text("""SELECT COALESCE(s.human_text,s.proposed_text) raw_text,
                    l.content_type human_class,l.transaction_type human_transaction
                    FROM alliance_gold_span_labels l JOIN alliance_gold_spans s ON s.span_id=l.span_id
                    WHERE l.active=TRUE AND s.span_status='ACTIVE'
                      AND COALESCE(s.human_text,s.proposed_text) IS NOT NULL""")).mappings():
                    out.append({"raw_text":r["raw_text"],"class":r["human_class"],
                                "transaction":("AMBIGUOUS" if r["human_transaction"]=="BOTH" else (r["human_transaction"] or "UNKNOWN")),
                                "ownership":("NOT_OWNED" if r["human_class"]=="NOISE" else ("OWNED" if r["human_class"] in {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT"} else "AMBIGUOUS")),
                                "source":"GOLD"})
            except Exception: pass

        # Discover older HUMAN blind-audit tables dynamically. Current V4 is explicitly excluded.
        for t in tables:
            if t.startswith("alliance_championship_v410") or t.startswith("alliance_automation_v42"): continue
            cols=_cols(conn,t)
            need={"raw_text","human_class","human_transaction","human_ownership"}
            if not need.issubset(cols): continue
            try:
                q=f"""SELECT raw_text,human_class,human_transaction,human_ownership
                      FROM {t}
                      WHERE human_class IS NOT NULL AND human_transaction IS NOT NULL AND human_ownership IS NOT NULL"""
                for r in conn.execute(text(q)).mappings():
                    out.append({"raw_text":r["raw_text"],"class":r["human_class"],"transaction":r["human_transaction"],
                                "ownership":r["human_ownership"],"source":t})
            except Exception: pass
    # dedupe exact truth/text
    seen=set(); ded=[]
    for r in out:
        k=(hashlib.sha256(_norm(r["raw_text"]).encode()).hexdigest(),r["class"],r["transaction"],r["ownership"])
        if k not in seen:
            seen.add(k); ded.append(r)
    return ded

def judge_human_precedent(engine,raw):
    target=_tokens(raw); n=_norm(raw)
    if not target: return (None,None,None,.5,.5,.5,{"rule":"E_NO_TOKENS"})
    candidates=[]
    decisive=["for sale","for rent","available for lease","required","requirement","wanted","pre leased","pre rented","demand","asking","budget","owner wants"]
    for r in _human_precedents(engine):
        toks=_tokens(r["raw_text"])
        if not toks: continue
        jac=len(target&toks)/max(len(target|toks),1)
        rn=_norm(r["raw_text"])
        phrase=sum(.035 for q in decisive if q in n and q in rn)
        score=min(1.0,jac+phrase)
        if score>=.28: candidates.append((score,r))
    candidates.sort(key=lambda x:x[0],reverse=True)
    if not candidates:
        return (None,None,None,.55,.55,.55,{"rule":"E_NO_STRONG_PRECEDENT"})

    top=candidates[:7]
    # weighted field votes; confidence is evidence-weighted, not arbitrary agreement.
    def field_vote(field):
        weights=defaultdict(float); total=0.0
        for score,r in top:
            val=r.get(field)
            if not val: continue
            w=score*score
            weights[val]+=w; total+=w
        if not weights or total<=0: return None,0.0,{}
        val,w=max(weights.items(),key=lambda kv:kv[1])
        share=w/total
        top_similarity=top[0][0]
        # Analogy cannot be a strong veto unless both semantic similarity and weighted agreement are high.
        conf=min(.995,.72 + .18*share + .10*top_similarity)
        if top_similarity<.42: conf=min(conf,.93)
        if share<.72: conf=min(conf,.92)
        return val,conf,dict(weights)
    c,cc,cv=field_vote("class"); tx,tc,tv=field_vote("transaction"); o,oc,ov=field_vote("ownership")
    return (c,tx,o,cc,tc,oc,{"rule":"E_CALIBRATED_HUMAN_PRECEDENT","matches":len(candidates),
                              "top_similarity":round(top[0][0],4),"class_weights":cv,"tx_weights":tv,"ownership_weights":ov,
                              "sources":[r["source"] for _,r in top]})

# ---------------- Judge F: section/intent hierarchy ----------------
def judge_intent_hierarchy(raw):
    n=_norm(raw); s=_signals(raw); group,ge=_group_strength(raw)
    ev={"rule":"F_INTENT_HIERARCHY","group":ge}
    # Explicit "required/wanted" is a buyer/tenant requirement even with specific property specs.
    req=bool(re.search(r"\b(?:required|requirement|wanted|need(?:ed)?|client wants?|buyer required)\b",n))
    availability=bool(re.search(r"\b(?:available|avl|for sale|for rent|available for lease|deal available|get(?:ting)? vacated|for showing)\b",n))
    if req and not availability:
        rental=bool(re.search(r"\b(?:rent|rental|lease)\b",n))
        sale=bool(re.search(r"\b(?:buy|purchase|buyer)\b",n) or re.search(r"\bbudget\b.{0,35}\d+(?:\.\d+)?\s*(?:cr|crore)",n))
        tx="RENT" if rental else ("SALE" if sale else "UNKNOWN")
        return ("REQUIREMENT",tx,"OWNED",.997,.996 if tx!="UNKNOWN" else .96,.999,ev)
    if group:
        tx="AMBIGUOUS" if ge["mixed"] else ("SALE" if s["sale"] or s["capital"] else ("RENT" if s["rent"] else "UNKNOWN"))
        return ("INVENTORY_GROUP",tx,"OWNED",.995,.993 if tx!="UNKNOWN" else .96,.999,ev)
    if s["tenancy"] and s["capital"]:
        return ("PROPERTY_AVAILABILITY","SALE","OWNED",.997,.999,.999,ev)
    if s["property"] and availability:
        tx="SALE" if s["sale"] or (s["capital"] and not s["rent"]) else ("RENT" if s["rent"] else "UNKNOWN")
        return ("PROPERTY_AVAILABILITY",tx,"OWNED",.994,.993 if tx!="UNKNOWN" else .96,.999,ev)
    if (s["greeting"] or s["admin"]) and not (s["property"] or req or availability):
        return ("NOISE","UNKNOWN","NOT_OWNED",.997,.999,.999,ev)
    return (None,None,None,.55,.55,.55,{"rule":"F_ABSTAIN"})

def _adapt_old(name,j):
    # v420 judges return class,tx,ownership,one confidence,evidence.
    c,tx,o,cf,ev=j
    return (c,tx,o,cf,cf,cf,{"source_judge":name,**(ev or {})})

def _judges(engine,raw):
    return {
      "A_EVIDENCE_CONTRACT":_adapt_old("A",v420.judge_evidence(raw)),
      "B_COUNTERFACTUAL_CRITIC":_adapt_old("B",v420.judge_critic(raw)),
      "D_CRE_DECISION_GRAPH":judge_cre_graph(raw),
      "E_HUMAN_PRECEDENT":judge_human_precedent(engine,raw),
      "F_INTENT_HIERARCHY":judge_intent_hierarchy(raw),
    }

def _persist(engine,audit_id,judges):
    with engine.begin() as conn:
        for name,j in judges.items():
            c,tx,o,cc,tc,oc,ev=j
            conn.execute(text("""INSERT INTO alliance_automation_v421_judgments
              (judgment_id,audit_id,exam_version,judge_name,predicted_class,predicted_transaction,predicted_ownership,
               class_confidence,transaction_confidence,ownership_confidence,evidence,judge_version)
              VALUES(:id,:aid,:e,:jn,:c,:tx,:o,:cc,:tc,:oc,CAST(:ev AS JSONB),:v)
              ON CONFLICT(audit_id,judge_name,judge_version) DO NOTHING"""),
              {"id":str(uuid.uuid4()),"aid":str(audit_id),"e":EXAM_VERSION,"jn":name,"c":c,"tx":tx,"o":o,
               "cc":cc,"tc":tc,"oc":oc,"ev":_j(ev),"v":VERSION})

def _field_consensus(judges,field):
    idx={"class":0,"transaction":1,"ownership":2}[field]
    cidx={"class":3,"transaction":4,"ownership":5}[field]
    votes=[]
    for name,j in judges.items():
        val=j[idx]; cf=float(j[cidx] or 0)
        if val is not None and cf>=.90:
            votes.append((name,val,cf))
    if not votes:
        return {"status":"UNRESOLVED","reason":"NO_QUALIFIED_VOTES"}
    by=defaultdict(list)
    for name,val,cf in votes: by[val].append((name,cf))
    winner,arr=max(by.items(),key=lambda kv:(len(kv[1]),sum(x[1] for x in kv[1])))
    count=len(arr); avg=sum(x[1] for x in arr)/count; mn=min(x[1] for x in arr)
    dissent=[(n,v,cf) for n,v,cf in votes if v!=winner]

    # Accept 3+ qualified independent votes, or A+B+D/F strong agreement.
    core_names={n for n,_ in arr}
    core_semantic=len(core_names & {"A_EVIDENCE_CONTRACT","B_COUNTERFACTUAL_CRITIC","D_CRE_DECISION_GRAPH","F_INTENT_HIERARCHY"})
    strong = (count>=3 and avg>=.965 and mn>=.94) or (core_semantic>=3 and avg>=.97)
    # A calibrated human precedent can reinforce but not veto unless >= .96.
    hp=next((x for x in votes if x[0]=="E_HUMAN_PRECEDENT"),None)
    if hp and hp[1]!=winner and hp[2]>=.96 and count<4:
        strong=False
    if strong:
        return {"status":"RESOLVED","value":winner,"confidence":round(min(avg,mn+.015),4),
                "votes":[{"judge":n,"value":winner,"confidence":cf} for n,cf in arr],
                "dissent":[{"judge":n,"value":v,"confidence":cf} for n,v,cf in dissent]}
    return {"status":"UNRESOLVED","majority":winner,"count":count,"avg_confidence":round(avg,4),
            "votes":[{"judge":n,"value":v,"confidence":cf} for n,v,cf in votes]}

def _consensus(judges):
    parts={f:_field_consensus(judges,f) for f in ("class","transaction","ownership")}
    if all(x["status"]=="RESOLVED" for x in parts.values()):
        return {"status":"AUTO_RESOLVED","class":parts["class"]["value"],"transaction":parts["transaction"]["value"],
                "ownership":parts["ownership"]["value"],
                "class_confidence":parts["class"]["confidence"],"transaction_confidence":parts["transaction"]["confidence"],
                "ownership_confidence":parts["ownership"]["confidence"],"fields":parts}
    return {"status":"EXCEPTION","fields":parts,"reason":"ONE_OR_MORE_FIELDS_UNRESOLVED"}

def adjudicate(engine):
    _install(engine); v410.freeze_exam(engine)
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""SELECT audit_id,ordinal,raw_text FROM alliance_championship_v410_cases
          WHERE exam_version=:e ORDER BY ordinal"""),{"e":EXAM_VERSION}).mappings()]
    auto=exc=0
    for r in rows:
        with engine.connect() as conn:
            st=conn.execute(text("SELECT status FROM alliance_automation_v421_truth WHERE audit_id=:id"),{"id":str(r["audit_id"])}).scalar()
        if st:
            auto+=st=="AUTO_RESOLVED"; exc+=st=="EXCEPTION"; continue
        judges=_judges(engine,r["raw_text"]); _persist(engine,r["audit_id"],judges); con=_consensus(judges)
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_automation_v421_truth
              (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,class_confidence,transaction_confidence,ownership_confidence,
               truth_source,consensus,status)
              VALUES(:id,:aid,:e,:c,:tx,:o,:cc,:tc,:oc,'FIELDWISE_MULTI_JUDGE_CALIBRATED',CAST(:con AS JSONB),:st)"""),
              {"id":str(uuid.uuid4()),"aid":str(r["audit_id"]),"e":EXAM_VERSION,"c":con.get("class"),"tx":con.get("transaction"),
               "o":con.get("ownership"),"cc":con.get("class_confidence",0),"tc":con.get("transaction_confidence",0),
               "oc":con.get("ownership_confidence",0),"con":_j(con),"st":con["status"]})
        auto+=con["status"]=="AUTO_RESOLVED"; exc+=con["status"]=="EXCEPTION"
    return {"status":"PASS","total":len(rows),"auto_resolved":int(auto),"exceptions":int(exc)}

def _rows(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text("""SELECT c.*,t.truth_class,t.truth_transaction,t.truth_ownership,t.status AS auto_status,t.consensus
          FROM alliance_championship_v410_cases c LEFT JOIN alliance_automation_v421_truth t ON t.audit_id=c.audit_id
          WHERE c.exam_version=:e ORDER BY c.ordinal"""),{"e":EXAM_VERSION}).mappings()]

def score(engine):
    adjudicate(engine); rows=_rows(engine)
    resolved=[]; unresolved=[]; auto=human=0
    for r in rows:
        if r["auto_status"]=="AUTO_RESOLVED":
            truth={"class":r["truth_class"],"transaction":r["truth_transaction"],"ownership":r["truth_ownership"]}; auto+=1
        elif r["review_status"]=="SAVED" and r["human_class"] and r["human_transaction"] and r["human_ownership"]:
            truth={"class":r["human_class"],"transaction":r["human_transaction"],"ownership":r["human_ownership"]}; human+=1
        else:
            unresolved.append({"audit_id":str(r["audit_id"]),"ordinal":r["ordinal"],"consensus":r.get("consensus")}); continue
        pred={"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        resolved.append((r,truth,pred))

    if unresolved:
        return {"version":VERSION,"exam_version":EXAM_VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,
                "unresolved":len(unresolved),"unresolved_cases":unresolved,"manual_work_required":len(unresolved),
                "certification_gate":"TRUTH_ESCALATOR_AWAITING_ONLY_IRREDUCIBLE_EXCEPTIONS",
                "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}

    fs={f:[0,0] for f in ("class","transaction","ownership")}; errors=[]; case_ok=0
    for r,t,p in resolved:
        ok=True
        for f in fs:
            fs[f][1]+=1
            if t[f]==p[f]: fs[f][0]+=1
            else: ok=False; errors.append({"ordinal":r["ordinal"],"field":f,"truth":t[f],"student":p[f]})
        case_ok+=int(ok)
    comparable=sum(x[1] for x in fs.values()); correct=sum(x[0] for x in fs.values())
    acc=round(100*correct/max(comparable,1),4); fields={k:round(100*v[0]/max(v[1],1),4) for k,v in fs.items()}
    case_acc=round(100*case_ok/max(len(resolved),1),4)
    gate="AUTOMATED_INDEPENDENT_V4_PASS" if acc>=OVERALL_PASS and all(v>=FIELD_PASS for v in fields.values()) else "AUTOMATED_INDEPENDENT_V4_HOLD"
    payload={"version":VERSION,"exam_version":EXAM_VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,"unresolved":0,
             "correct_fields":correct,"comparable_fields":comparable,"accuracy":acc,"field_accuracy":fields,"case_accuracy":case_acc,
             "errors":errors,"certification_gate":gate,"manual_work_required":0,
             "truth_policy":"Field-wise consensus from independent evidence, critic, CRE graph, calibrated prior human precedent, and intent-hierarchy judges.",
             "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    th=hashlib.sha256(json.dumps([(str(r["audit_id"]),t["class"],t["transaction"],t["ownership"]) for r,t,_ in resolved],separators=(",",":")).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_automation_v421_results
          (result_id,exam_version,total_cases,auto_resolved,human_resolved,unresolved,comparable_fields,correct_fields,overall_accuracy,
           class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,certification_gate,truth_hash,result)
          VALUES(:id,:e,:tot,:a,:h,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
          ON CONFLICT(exam_version) DO NOTHING"""),
          {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"tot":len(rows),"a":auto,"h":human,"cmp":comparable,"cor":correct,"oa":acc,
           "ca":fields["class"],"ta":fields["transaction"],"ow":fields["ownership"],"casea":case_acc,"gate":gate,"th":th,"res":_j(payload)})
    with engine.connect() as conn:
        stored=conn.execute(text("SELECT result FROM alliance_automation_v421_results WHERE exam_version=:e"),{"e":EXAM_VERSION}).scalar()
    return stored or payload

def _first_exception(engine):
    for r in _rows(engine):
        if r["auto_status"]=="EXCEPTION" and r["review_status"]=="OPEN": return r
    return None

def _dashboard(engine):
    s=score(engine); exc=_first_exception(engine)
    if s.get("unresolved",0)==0:
        action="<div class='ok'>✓ Full V4 truth adjudication completed automatically. No manual work required.</div>"
    elif exc:
        action=f"""<div class='warn'><b>Automation has reduced the exam to {s['unresolved']} genuinely unresolved exception(s).</b>
        Do not review anything else.</div><p>First remaining exception: Case {exc['ordinal']}</p>
        <pre>{html.escape(exc['raw_text'])}</pre><details><summary>Why automation abstained</summary><pre>{html.escape(json.dumps(foundation._json_safe(exc.get('consensus')),ensure_ascii=False,indent=2))}</pre></details>"""
    else:
        action="<div class='warn'>Exception state needs inspection.</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Truth Escalator 4.2.1</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1100px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
    .card{{background:white;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:28px;margin-top:8px}}
    .ok{{background:#e8f8ee;padding:15px;border-radius:10px;font-weight:700;margin:18px 0}}.warn{{background:#fff4cf;padding:15px;border-radius:10px;margin:18px 0}}
    pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px}}</style></head><body>
    <h1>Alliance Automation Truth Escalator 4.2.1</h1><p>Field-wise multi-judge adjudication with calibrated immutable human precedents. Student V4 predictions remain frozen.</p>
    <div class='grid'><div class='card'>V4 Cases<strong>{s.get('total',0)}</strong></div><div class='card'>Auto Resolved<strong>{s.get('auto_resolved',0)}</strong></div>
    <div class='card'>Human Resolved<strong>{s.get('human_resolved',0)}</strong></div><div class='card'>Manual Remaining<strong>{s.get('unresolved',0)}</strong></div>
    <div class='card'>Accuracy<strong>{s.get('accuracy','—')}</strong></div><div class='card'>Gate<strong style='font-size:16px'>{html.escape(str(s.get('certification_gate')))}</strong></div></div>
    {action}<h2>Machine Report</h2><pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine); adjudicate(engine)
    if not foundation._route_exists(app,"/api/property-brain/automation-v421/status"):
        @app.get("/api/property-brain/automation-v421/status")
        def status_v421(): return score(engine)
    if not foundation._route_exists(app,"/api/property-brain/automation-v421/run"):
        @app.post("/api/property-brain/automation-v421/run")
        def run_v421(): return score(engine)
    if not foundation._route_exists(app,"/property-brain/automation-v421"):
        @app.get("/property-brain/automation-v421",response_class=HTMLResponse)
        def page_v421(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/automation-v421",
            "policy":"AUTOMATE_UNTIL_ONLY_IRREDUCIBLE_EXCEPTION_REMAINS","student_tuning":0,
            "production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
