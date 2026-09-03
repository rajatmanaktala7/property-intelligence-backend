from __future__ import annotations

import hashlib
import html
import json
import math
import re
import uuid
from collections import Counter, defaultdict

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_championship_v410 as v410

VERSION = "4.2.0-ALLIANCE-AUTOMATION-MACHINE"
MODE = "INDEPENDENT_MULTI_JUDGE_AUTO_ADJUDICATION_EXCEPTION_ONLY_NO_STUDENT_TUNING"
ENGINE_VERSION = "ALLIANCE_AUTOMATION_MACHINE_V420"
EXAM_VERSION = v410.EXAM_VERSION

AUTO_TRUTH_MIN = 0.98
OVERALL_PASS = 95.0
FIELD_PASS = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_automation_v420_judgments(
judgment_id UUID PRIMARY KEY,
audit_id UUID NOT NULL,
exam_version TEXT NOT NULL,
judge_name TEXT NOT NULL,
predicted_class TEXT,
predicted_transaction TEXT,
predicted_ownership TEXT,
confidence NUMERIC(6,4) NOT NULL,
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
judge_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(audit_id,judge_name,judge_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_automation_v420_truth(
truth_id UUID PRIMARY KEY,
audit_id UUID NOT NULL UNIQUE,
exam_version TEXT NOT NULL,
truth_class TEXT,
truth_transaction TEXT,
truth_ownership TEXT,
truth_confidence NUMERIC(6,4),
truth_source TEXT NOT NULL,
consensus JSONB NOT NULL DEFAULT '{}'::jsonb,
status TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_automation_v420_results(
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
def _app(core): return getattr(core, "app", None) or core
def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _norm(raw):
    s=(raw or "").lower()
    s=re.sub(r"https?://\S+"," url ",s,flags=re.I)
    s=re.sub(r"[^\w\u0900-\u097f₹+./@%\- ]+"," ",s,flags=re.UNICODE)
    return re.sub(r"\s+"," ",s).strip()

def _lines(raw):
    return [re.sub(r"\s+"," ",x).strip() for x in (raw or "").splitlines() if x.strip()]

def _signals(raw):
    n=_norm(raw)
    return {
      "sale": bool(re.search(r"\b(?:for sale|available for sale|sale inventory|deal available on sale|outright|resale|asking price|owner wants|demand\s*(?:@|:|-)?\s*(?:₹|rs\.?)?\s*\d)\b|(?:अर्जेंट|urgent)\s*सेल|\bसेल\b",n)),
      "rent": bool(re.search(r"\b(?:for rent|available for rent|available for lease|available on lease|to let|leave and license|long lease|asking rent|rent\s*(?:@|:|₹|rs|\d)|showing rent)\b",n)),
      "req": bool(re.search(r"\b(?:required|requirement|wanted|looking for|need(?:ed)?|seeking|client wants?|want(?:s)? to purchase|buyer required|tenant requirement)\b",n)),
      "avail": bool(re.search(r"\b(?:available|avl|deal available|getting vacated|vacant|ready to move|for showing|showing|call for visit|site visit)\b",n)),
      "property": bool(re.search(r"\b(?:bhk|flat|apartment|villa|plot|shop|office|basement|floor|building|kothi|farmhouse|penthouse|showroom|warehouse|commercial space|retail|sqft|sq ft|sq yds|syds|sqmt|sqm|yards?)\b|गज|बिल्डिंग",n)),
      "tenancy": bool(re.search(r"\b(?:pre[- ]?leased|pre[- ]?rented|freshly leased|leased to|tenant|rental income|roi)\b",n)),
      "capital": bool(re.search(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore)\b|\b(?:demand|asking|price|owner wants)\b.{0,18}\d",n)),
      "monthly": bool(re.search(r"\brent(?:al)?\b.{0,20}(?:₹|rs\.?)?\s*\d|\b\d+(?:\.\d+)?\s*(?:k|lac|lakh|l)\s*(?:pm|per month|/month)\b",n)),
      "greeting": bool(re.search(r"\b(?:good morning|good evening|good night|best wishes|congratulations|raksha bandhan|rakshabandhan|happy birthday)\b|शुभकामनाएं",n)),
      "admin": bool(re.search(r"\b(?:this group|group for rented properties|request everyone|remove such content|remove .*members|please don't post)\b",n)),
      "ideal": bool(re.search(r"\b(?:ideal for|suitable for|perfect for)\b",n)),
    }

def _group_strength(raw):
    n=_norm(raw); lines=_lines(raw)
    sale_headers=len(re.findall(r"\b(?:for sale|available for sale|sale inventory)\b",n))
    rent_headers=len(re.findall(r"\b(?:for rent|available for rent|available for lease)\b",n))
    prices=len(re.findall(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh|l)\b",n))
    projects=len(re.findall(r"\b(?:dlf|m3m|emaar|aipl|ireo|bestech|tulip|elan|ats|unitech|mahindra|vipul|suncity|sobha|godrej|hero homes|krisumi|raheja)\b",n))
    options=len(re.findall(r"\b(?:upper ground|ug|ground|first|second|third|top|terrace|basement|\d(?:\.\d)?\s*bhk)\b",n))
    bullets=sum(1 for x in lines if re.match(r"^\s*(?:[-•▫️]|\d+[.)])",x))
    explicit=bool(re.search(r"\b(?:inventory|inventories|multiple options|multiple units|asset deals|many options|villas available on long lease)\b",n))
    mixed=sale_headers>0 and rent_headers>0
    strong= mixed or explicit or (prices>=2 and options>=2) or (projects>=3 and prices>=2) or (bullets>=3 and prices>=2)
    return strong, {"sale_headers":sale_headers,"rent_headers":rent_headers,"prices":prices,"projects":projects,"options":options,"bullets":bullets,"explicit":explicit,"mixed":mixed}

# ---------------- Judge A: Evidence Contract ----------------
def judge_evidence(raw):
    s=_signals(raw); n=_norm(raw); group,ge=_group_strength(raw)
    cre=s["property"] or s["sale"] or s["rent"] or s["req"] or s["tenancy"]
    if (s["greeting"] or s["admin"]) and not (s["property"] or s["sale"] or s["rent"] or s["req"]):
        return ("NOISE","UNKNOWN","NOT_OWNED",0.995,{"rule":"A_NOISE","signals":s})
    if re.fullmatch(r"(?:url\s*){1,4}",n):
        return ("NOISE","UNKNOWN","NOT_OWNED",0.995,{"rule":"A_URL_ONLY"})
    if group:
        cls="INVENTORY_GROUP"
    elif s["req"] and not s["avail"] and not s["sale"] and not s["rent"]:
        cls="REQUIREMENT"
    elif s["req"] and not s["avail"] and not s["property"]:
        cls="REQUIREMENT"
    elif s["property"] or (s["tenancy"] and s["capital"]):
        cls="PROPERTY_AVAILABILITY"
    else:
        return (None,None,None,0.55,{"rule":"A_ABSTAIN","signals":s})

    if cls=="REQUIREMENT":
        if s["rent"]: tx="RENT"
        elif s["sale"] or s["capital"] or re.search(r"\b(?:purchase|buy|buyer)\b",n): tx="SALE"
        else: tx="UNKNOWN"
    elif ge["mixed"]:
        tx="AMBIGUOUS"
    elif s["tenancy"] and s["capital"]:
        tx="SALE"
    elif s["sale"] or (s["capital"] and cls!="REQUIREMENT"):
        tx="SALE"
    elif s["rent"] or s["monthly"]:
        tx="RENT"
    else:
        tx="UNKNOWN"
    return (cls,tx,"OWNED",0.992 if tx!="UNKNOWN" else 0.965,{"rule":"A_EVIDENCE_CONTRACT","signals":s,"group":ge})

# ---------------- Judge B: Counterfactual Critic ----------------
def judge_critic(raw):
    n=_norm(raw); s=_signals(raw); group,ge=_group_strength(raw)
    # Remove marketing suitability phrases. If core still advertises an asset, it cannot be a requirement.
    core=re.sub(r"\b(?:ideal for|suitable for|perfect for)\b.{0,80}"," ",n)
    availability_core=bool(re.search(r"\b(?:available|for rent|for sale|lease|rent|demand|asking|owner wants|getting vacated|showing)\b",core))
    demand_core=bool(re.search(r"\b(?:required|requirement|wanted|client wants?|need(?:ed)?|seeking|looking for)\b",core))
    if s["greeting"] and not availability_core and not demand_core and not s["property"]:
        return ("NOISE","UNKNOWN","NOT_OWNED",0.99,{"rule":"B_GREETING_COUNTERFACTUAL"})
    if s["admin"] and not availability_core and not demand_core:
        return ("NOISE","UNKNOWN","NOT_OWNED",0.99,{"rule":"B_ADMIN_COUNTERFACTUAL"})
    if group:
        cls="INVENTORY_GROUP"
    elif demand_core and not availability_core:
        cls="REQUIREMENT"
    elif availability_core and (s["property"] or s["tenancy"] or s["capital"]):
        cls="PROPERTY_AVAILABILITY"
    elif s["property"] and (s["sale"] or s["rent"] or s["capital"]):
        cls="PROPERTY_AVAILABILITY"
    else:
        return (None,None,None,0.60,{"rule":"B_ABSTAIN"})

    if cls=="REQUIREMENT":
        tx="SALE" if (s["sale"] or s["capital"] or re.search(r"\b(?:buy|purchase|buyer)\b",n)) else ("RENT" if s["rent"] else "UNKNOWN")
    elif ge["mixed"]:
        tx="AMBIGUOUS"
    elif s["tenancy"] and s["capital"]:
        tx="SALE"
    elif s["sale"] or s["capital"]:
        tx="SALE"
    elif s["rent"] or s["monthly"]:
        tx="RENT"
    else:
        tx="UNKNOWN"
    return (cls,tx,"OWNED",0.987 if tx!="UNKNOWN" else 0.95,{"rule":"B_COUNTERFACTUAL_CRITIC","group":ge})

# ---------------- Judge C: Immutable Gold Analogy ----------------
def _tokens(s):
    n=_norm(s)
    stop={"for","the","and","with","from","this","that","are","is","to","of","in","on","at","a","an","please","call","contact"}
    return {x for x in re.findall(r"[a-z\u0900-\u097f]{3,}|\d+(?:\.\d+)?",n) if x not in stop}

def _gold_examples(engine):
    with engine.connect() as conn:
        # Human Gold only. Do not use automated Silver or any V4 truth.
        rows=[dict(r) for r in conn.execute(text("""
          SELECT COALESCE(s.human_text,s.proposed_text) AS raw_text,
                 l.content_type,l.transaction_type,l.human_confidence
          FROM alliance_gold_span_labels l
          JOIN alliance_gold_spans s ON s.span_id=l.span_id
          WHERE l.active=TRUE
            AND s.span_status='ACTIVE'
            AND COALESCE(s.human_text,s.proposed_text) IS NOT NULL
          ORDER BY l.created_at
        """)).mappings()]
    return rows

def judge_gold_analogy(engine,raw):
    target=_tokens(raw)
    if not target: return (None,None,None,0.50,{"rule":"C_NO_TOKENS"})
    best=[]
    for r in _gold_examples(engine):
        toks=_tokens(r["raw_text"])
        if not toks: continue
        inter=len(target&toks); union=len(target|toks)
        jac=inter/max(union,1)
        # reward decisive CRE phrase overlap
        phrase=0.0
        a=_norm(raw); b=_norm(r["raw_text"])
        for q in ("for sale","for rent","available for lease","required","requirement","pre leased","pre rented","demand","asking","ideal for"):
            if q in a and q in b: phrase+=0.05
        score=min(1.0,jac+phrase)
        if score>=0.22:
            best.append((score,r))
    best.sort(key=lambda x:x[0],reverse=True)
    if len(best)<2:
        return (None,None,None,0.60,{"rule":"C_INSUFFICIENT_ANALOGS","matches":len(best)})
    top=best[:5]
    class_votes=Counter(r["content_type"] for _,r in top if r["content_type"])
    tx_votes=Counter((r["transaction_type"] or "UNKNOWN") for _,r in top)
    cls,cnt=class_votes.most_common(1)[0]
    tx,tcnt=tx_votes.most_common(1)[0]
    # historical BOTH is legacy; parent audit canonical truth is AMBIGUOUS when both transactions coexist.
    if tx=="BOTH": tx="AMBIGUOUS"
    agreement=min(cnt/len(top),tcnt/len(top))
    conf=min(0.985,0.80+0.15*agreement+0.05*min(top[0][0],1.0))
    own="NOT_OWNED" if cls=="NOISE" else ("OWNED" if cls in {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT"} else "AMBIGUOUS")
    return (cls,tx,own,conf,{"rule":"C_IMMUTABLE_GOLD_ANALOGY","top_scores":[round(x[0],4) for x in top],
                              "class_votes":dict(class_votes),"tx_votes":dict(tx_votes)})

def _judge_case(engine,row):
    judges={
      "EVIDENCE_CONTRACT":judge_evidence(row["raw_text"]),
      "COUNTERFACTUAL_CRITIC":judge_critic(row["raw_text"]),
      "IMMUTABLE_GOLD_ANALOGY":judge_gold_analogy(engine,row["raw_text"]),
    }
    return judges

def _persist_judges(engine,audit_id,judges):
    with engine.begin() as conn:
        for name,(c,tx,o,cf,ev) in judges.items():
            conn.execute(text("""INSERT INTO alliance_automation_v420_judgments
            (judgment_id,audit_id,exam_version,judge_name,predicted_class,predicted_transaction,predicted_ownership,confidence,evidence,judge_version)
            VALUES(:id,:aid,:e,:jn,:c,:tx,:o,:cf,CAST(:ev AS JSONB),:v)
            ON CONFLICT(audit_id,judge_name,judge_version) DO NOTHING"""),
            {"id":str(uuid.uuid4()),"aid":str(audit_id),"e":EXAM_VERSION,"jn":name,"c":c,"tx":tx,"o":o,"cf":cf,"ev":_j(ev),"v":VERSION})

def _consensus(judges):
    valid=[(name,j) for name,j in judges.items() if j[0] is not None]
    if len(valid)<2:
        return {"status":"EXCEPTION","reason":"FEWER_THAN_TWO_INDEPENDENT_JUDGES","confidence":0.0}
    triples=Counter((j[0],j[1],j[2]) for _,j in valid)
    triple,count=triples.most_common(1)[0]
    agreeing=[(name,j) for name,j in valid if (j[0],j[1],j[2])==triple]
    disagree=[name for name,j in valid if (j[0],j[1],j[2])!=triple]
    avg=sum(j[3] for _,j in agreeing)/len(agreeing)
    mincf=min(j[3] for _,j in agreeing)
    # Three-way agreement is strongest. Two-way agreement can auto-resolve only if
    # Evidence Contract + Critic agree and Gold does not actively contradict at high confidence.
    names={n for n,_ in agreeing}
    gold=judges["IMMUTABLE_GOLD_ANALOGY"]
    strong_two=(names=={"EVIDENCE_CONTRACT","COUNTERFACTUAL_CRITIC"} and count==2 and
                (gold[0] is None or gold[3]<0.95))
    all_three=(count==3)
    confidence=min(avg,mincf+0.01)
    if (all_three and confidence>=AUTO_TRUTH_MIN) or (strong_two and confidence>=AUTO_TRUTH_MIN):
        return {"status":"AUTO_RESOLVED","class":triple[0],"transaction":triple[1],"ownership":triple[2],
                "confidence":round(confidence,4),"agreeing_judges":sorted(names),"disagreeing_judges":disagree}
    return {"status":"EXCEPTION","reason":"JUDGE_DISAGREEMENT_OR_LOW_CONFIDENCE","confidence":round(confidence,4),
            "majority":{"class":triple[0],"transaction":triple[1],"ownership":triple[2],"count":count},
            "agreeing_judges":sorted(names),"disagreeing_judges":disagree}

def adjudicate(engine):
    _install(engine)
    freeze=v410.freeze_exam(engine)
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT audit_id,ordinal,raw_text,predicted_class,predicted_transaction,predicted_ownership
          FROM alliance_championship_v410_cases
          WHERE exam_version=:e ORDER BY ordinal
        """),{"e":EXAM_VERSION}).mappings()]
    auto=0; exceptions=0
    for row in rows:
        with engine.connect() as conn:
            existing=conn.execute(text("SELECT status FROM alliance_automation_v420_truth WHERE audit_id=:id"),{"id":str(row["audit_id"])}).scalar()
        if existing:
            auto += 1 if existing=="AUTO_RESOLVED" else 0
            exceptions += 1 if existing=="EXCEPTION" else 0
            continue
        judges=_judge_case(engine,row)
        _persist_judges(engine,row["audit_id"],judges)
        cons=_consensus(judges)
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_automation_v420_truth
            (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,truth_confidence,truth_source,consensus,status)
            VALUES(:id,:aid,:e,:c,:tx,:o,:cf,:src,CAST(:con AS JSONB),:st)"""),
            {"id":str(uuid.uuid4()),"aid":str(row["audit_id"]),"e":EXAM_VERSION,
             "c":cons.get("class"),"tx":cons.get("transaction"),"o":cons.get("ownership"),"cf":cons.get("confidence",0.0),
             "src":"INDEPENDENT_MULTI_JUDGE_CONSENSUS","con":_j(cons),"st":cons["status"]})
        auto += 1 if cons["status"]=="AUTO_RESOLVED" else 0
        exceptions += 1 if cons["status"]=="EXCEPTION" else 0
    return {"status":"PASS","exam_freeze":freeze,"total":len(rows),"auto_resolved":auto,"exceptions":exceptions}

def _blended_truth(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT c.audit_id,c.ordinal,c.predicted_class,c.predicted_transaction,c.predicted_ownership,
                 c.human_class,c.human_transaction,c.human_ownership,c.review_status,
                 t.truth_class,t.truth_transaction,t.truth_ownership,t.truth_confidence,t.status AS auto_status
          FROM alliance_championship_v410_cases c
          LEFT JOIN alliance_automation_v420_truth t ON t.audit_id=c.audit_id
          WHERE c.exam_version=:e ORDER BY c.ordinal
        """),{"e":EXAM_VERSION}).mappings()]
    return rows

def score(engine):
    adjudicate(engine)
    rows=_blended_truth(engine)
    resolved=[]
    unresolved=[]
    auto=human=0
    for r in rows:
        if r["auto_status"]=="AUTO_RESOLVED":
            truth={"class":r["truth_class"],"transaction":r["truth_transaction"],"ownership":r["truth_ownership"]}
            auto+=1
        elif r["review_status"]=="SAVED" and r["human_class"] and r["human_transaction"] and r["human_ownership"]:
            truth={"class":r["human_class"],"transaction":r["human_transaction"],"ownership":r["human_ownership"]}
            human+=1
        else:
            unresolved.append({"audit_id":str(r["audit_id"]),"ordinal":r["ordinal"]})
            continue
        pred={"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        resolved.append((r,truth,pred))

    if unresolved:
        return {"version":VERSION,"exam_version":EXAM_VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,
                "unresolved":len(unresolved),"unresolved_cases":unresolved,
                "certification_gate":"AUTOMATION_EXCEPTION_ONLY_AWAITING_IRREDUCIBLE_CASES",
                "manual_work_required":len(unresolved),
                "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0,"student_tuning":0}}

    fstats={f:[0,0] for f in ("class","transaction","ownership")}
    case_ok=0; errors=[]
    for r,truth,pred in resolved:
        allok=True
        for f in fstats:
            fstats[f][1]+=1
            if truth[f]==pred[f]: fstats[f][0]+=1
            else:
                allok=False; errors.append({"ordinal":r["ordinal"],"field":f,"truth":truth[f],"student":pred[f]})
        if allok: case_ok+=1
    comparable=sum(v[1] for v in fstats.values()); correct=sum(v[0] for v in fstats.values())
    overall=round(100*correct/max(comparable,1),4)
    fields={k:round(100*v[0]/max(v[1],1),4) for k,v in fstats.items()}
    case_acc=round(100*case_ok/max(len(resolved),1),4)
    passed=overall>=OVERALL_PASS and all(v>=FIELD_PASS for v in fields.values())
    gate="AUTOMATED_INDEPENDENT_V4_PASS" if passed else "AUTOMATED_INDEPENDENT_V4_HOLD"
    payload={"version":VERSION,"exam_version":EXAM_VERSION,"total":len(rows),"auto_resolved":auto,"human_resolved":human,
             "unresolved":0,"correct_fields":correct,"comparable_fields":comparable,"accuracy":overall,
             "field_accuracy":fields,"case_accuracy":case_acc,"errors":errors,"certification_gate":gate,
             "manual_work_required":0,
             "truth_policy":"Independent multi-judge consensus; human truth used only for irreducible exceptions.",
             "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0,"student_tuning":0}}
    truth_hash=hashlib.sha256(json.dumps([(str(r["audit_id"]),t["class"],t["transaction"],t["ownership"]) for r,t,_ in resolved],
                                       separators=(",",":")).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_automation_v420_results
        (result_id,exam_version,total_cases,auto_resolved,human_resolved,unresolved,comparable_fields,correct_fields,
         overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,certification_gate,truth_hash,result)
        VALUES(:id,:e,:tot,:a,:h,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
        ON CONFLICT(exam_version) DO NOTHING"""),
        {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"tot":len(rows),"a":auto,"h":human,"cmp":comparable,"cor":correct,"oa":overall,
         "ca":fields["class"],"ta":fields["transaction"],"ow":fields["ownership"],"casea":case_acc,"gate":gate,"th":truth_hash,"res":_j(payload)})
    with engine.connect() as conn:
        stored=conn.execute(text("SELECT result FROM alliance_automation_v420_results WHERE exam_version=:e"),{"e":EXAM_VERSION}).scalar()
    return stored or payload

def _next_exception(engine):
    with engine.connect() as conn:
        return conn.execute(text("""
          SELECT c.audit_id,c.ordinal,c.raw_text,t.consensus
          FROM alliance_championship_v410_cases c
          JOIN alliance_automation_v420_truth t ON t.audit_id=c.audit_id
          WHERE c.exam_version=:e AND t.status='EXCEPTION' AND c.review_status='OPEN'
          ORDER BY c.ordinal LIMIT 1
        """),{"e":EXAM_VERSION}).mappings().first()

def _dashboard(engine):
    s=score(engine); exc=_next_exception(engine)
    if s.get("unresolved",0)==0:
        action="<div class='ok'>✓ Automation completed the entire V4 truth process. No manual work required.</div>"
    elif exc:
        action=f"""<div class='warn'><b>Automation exhausted.</b> Only {s['unresolved']} irreducible exception(s) remain. Human review is now justified.</div>
        <p>Case {exc['ordinal']}:</p><pre>{html.escape(exc['raw_text'])}</pre>
        <p>Use the existing Championship V4 page only for these remaining exception cases.</p>"""
    else:
        action="<div class='warn'>Unresolved cases remain but no reviewable exception was found. Check system status.</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Automation Machine 4.2</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1100px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
    .card{{background:white;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:28px;margin-top:8px}}
    .ok{{background:#e8f8ee;padding:15px;border-radius:10px;font-weight:700;margin:18px 0}}.warn{{background:#fff4cf;padding:15px;border-radius:10px;margin:18px 0}}
    pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px}}</style></head><body>
    <h1>Alliance Automation Machine 4.2</h1><p>Teacher → Critic → Immutable Gold Analogy → Consensus → Auto Truth → V4 Score → Exception only if unavoidable.</p>
    <div class='grid'><div class='card'>V4 Cases<strong>{s.get('total',0)}</strong></div><div class='card'>Auto Resolved<strong>{s.get('auto_resolved',0)}</strong></div>
    <div class='card'>Human Resolved<strong>{s.get('human_resolved',0)}</strong></div><div class='card'>Manual Remaining<strong>{s.get('unresolved',0)}</strong></div>
    <div class='card'>Accuracy<strong>{s.get('accuracy','—')}</strong></div><div class='card'>Gate<strong style='font-size:16px'>{html.escape(str(s.get('certification_gate')))}</strong></div></div>
    {action}<h2>Machine Report</h2><pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    adjudicate(engine)
    if not foundation._route_exists(app,"/api/property-brain/automation-v420/status"):
        @app.get("/api/property-brain/automation-v420/status")
        def status_v420(): return score(engine)
    if not foundation._route_exists(app,"/api/property-brain/automation-v420/run"):
        @app.post("/api/property-brain/automation-v420/run")
        def run_v420(): return score(engine)
    if not foundation._route_exists(app,"/property-brain/automation-v420"):
        @app.get("/property-brain/automation-v420",response_class=HTMLResponse)
        def page_v420(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/automation-v420",
            "policy":"NO_MANUAL_WORK_UNLESS_MULTI_JUDGE_CONSENSUS_CANNOT_RESOLVE",
            "student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
