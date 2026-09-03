from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import Counter

from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_mastery_finalizer_v340 as v340
import alliance_ownership_mastery_blind_v330 as v330
import alliance_training_gate_finalizer_v350 as v350

VERSION = "3.6.0-BLIND-FAILURE-LEARNING-NEW-UNSEEN-EXAM"
MODE = "FREEZE_EXAM_V1_GENERALIZE_FAILURES_FREEZE_NEW_UNSEEN_EXAM_V2_NO_PRODUCTION_WRITES"
ENGINE_VERSION = "ALLIANCE_BLIND_FAILURE_LEARNING_V360"
RULESET_VERSION = "BLIND_FAILURE_LEARNING_2026_09_03_V1"
EXAM_V1 = v340.AUDIT_VERSION
EXAM_V2 = "BLIND_AUDIT_V2_2026_09_03"
EXAM_V2_TARGET = 20
EXPERTISE_FIELD_ACCURACY = 95.0
EXPERTISE_CRITICAL_ACCURACY = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v360_exam_v1_freeze(
freeze_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
labeled_cases INTEGER NOT NULL,
comparable_fields INTEGER NOT NULL,
correct_fields INTEGER NOT NULL,
accuracy NUMERIC(8,4),
truth_hash TEXT NOT NULL,
frozen_result JSONB NOT NULL DEFAULT '{}'::jsonb,
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v360_lessons(
lesson_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL,
audit_id UUID NOT NULL,
blind_id UUID NOT NULL,
field_name TEXT NOT NULL,
predicted_value TEXT,
human_value TEXT NOT NULL,
lesson_code TEXT NOT NULL,
generalized_lesson TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(exam_version,audit_id,field_name,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v360_exam_v2_cases(
audit_id UUID PRIMARY KEY,
blind_id UUID NOT NULL,
exam_version TEXT NOT NULL,
priority INTEGER NOT NULL,
reason TEXT NOT NULL,
raw_text TEXT NOT NULL,
predicted_class TEXT,
predicted_transaction TEXT,
predicted_ownership TEXT,
prediction_confidence NUMERIC(6,2),
prediction_rule TEXT,
human_class TEXT,
human_transaction TEXT,
human_ownership TEXT,
human_confidence TEXT,
human_reason TEXT,
review_status TEXT NOT NULL DEFAULT 'OPEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(blind_id,exam_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v360_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
training_accuracy NUMERIC(8,4) NOT NULL,
training_mastery_gate BOOLEAN NOT NULL,
exam_v1_accuracy NUMERIC(8,4),
exam_v1_frozen BOOLEAN NOT NULL,
repair_accuracy NUMERIC(8,4),
exam_v2_total INTEGER NOT NULL,
exam_v2_labeled INTEGER NOT NULL,
exam_v2_accuracy NUMERIC(8,4),
expertise_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]


def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _loads(v, d):
    if v is None: return d
    if isinstance(v, (dict, list)): return v
    try: return json.loads(v)
    except Exception: return d

def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _fold(s):
    return unicodedata.normalize("NFKC", s or "")

def _norm(s):
    s = _fold(s).lower()
    s = re.sub(r"[^a-z0-9₹+./@\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _lines(s):
    return [re.sub(r"\s+", " ", x).strip() for x in _fold(s).splitlines() if x.strip()]

def _has_any(n, pats):
    return any(re.search(p, n, re.I) for p in pats)

def _property_identity(n):
    return bool(re.search(r"\b\d+\s*bhk\b|\b(?:apartment|flat|floor|villa|penthouse|plot|land|building|bldg|shop|office|kothi)\b", n))

def _price_signal(n):
    return bool(re.search(r"(?:₹|rs\.?|price|demand|owner.?s?\s+wants?|@)\s*[^\n]{0,25}\b\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh|k\b)", n, re.I) or re.search(r"\bprice\s*[:@]", n, re.I))

def _rent_signal(n):
    return bool(re.search(r"\b(?:for\s+rent|avail(?:able)?\s+for\s+rent|to[- ]?let|rent\s*[:@]|rental\s+inventory|deal\s+on\s+rent)\b", n, re.I))

def _sale_signal(n):
    return bool(re.search(r"\b(?:for\s+sale|inventory\s+for\s+sale|deals?\s+on\s+sale|outright|resale)\b", n, re.I))

def _pre_rented_sale(n):
    return bool(re.search(r"\b(?:pre[- ]?rented|rental\s+income)\b", n, re.I) and re.search(r"\bprice\s*[:@]", n, re.I))

def _requirement_signal(n):
    # "ideal for" describes suitable audience/use and is explicitly not a requirement by itself.
    if re.search(r"\bideal\s+for\b", n, re.I) and not re.search(r"\b(?:required|requirement|wanted|looking\s+for|need(?:ed)?|seeking)\b", n, re.I):
        return False
    return bool(re.search(r"\b(?:required|requirement|wanted|looking\s+for|need(?:ed)?|seeking)\b", n, re.I))

def _mixed_explicit_sections(raw):
    n = _norm(raw)
    rent_section = bool(re.search(r"\b(?:deal|inventory|exclusive\s+mandate)[^\n]{0,30}\brent\b", n, re.I))
    sale_section = bool(re.search(r"\b(?:deal|inventory|exclusive)[^\n]{0,30}\bsale\b", n, re.I))
    return rent_section and sale_section

def _portfolio_sale(raw):
    n = _norm(raw)
    # Pre-rented assets are an investment sale when each asset carries a price.
    return _pre_rented_sale(n)

def predict_message(raw_text):
    raw = raw_text or ""
    n = _norm(raw)
    ls = _lines(raw)

    if len(n) < 8:
        return {"class":"FRAGMENT","transaction":"UNKNOWN","ownership":"AMBIGUOUS","confidence":85.0,"rule":"V360_TINY_FRAGMENT"}

    req = _requirement_signal(n)
    prop = _property_identity(n)
    rent = _rent_signal(n)
    sale = _sale_signal(n)
    portfolio_sale = _portfolio_sale(raw)
    mixed = _mixed_explicit_sections(raw)
    money = _price_signal(n) or bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:cr|lac|lakh|k)\b", n))
    contact = bool(re.search(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b", re.sub(r"[\s-]", "", raw)))

    if req and not (sale or rent):
        cls = "REQUIREMENT"
        class_rule = "V360_TRUE_REQUIREMENT_INTENT"
    elif prop and (sale or rent or money or portfolio_sale):
        cls = "PROPERTY_AVAILABILITY"
        class_rule = "V360_COMPLETE_PROPERTY_EVIDENCE"
    elif (sale or rent or portfolio_sale) and len(ls) >= 3:
        cls = "PROPERTY_AVAILABILITY"
        class_rule = "V360_INVENTORY_AVAILABILITY_SCOPE"
    elif req:
        cls = "REQUIREMENT"
        class_rule = "V360_REQUIREMENT_FALLBACK"
    elif len(ls) <= 2:
        cls = "FRAGMENT"
        class_rule = "V360_SHORT_FRAGMENT"
    else:
        cls = "UNRESOLVED"
        class_rule = "V360_CLASS_ABSTAIN"

    if mixed:
        tx = "UNKNOWN"
        tx_rule = "V360_MIXED_SALE_RENT_SECTION_ABSTAIN"
    elif portfolio_sale:
        tx = "SALE"
        tx_rule = "V360_PRE_RENTED_INCOME_ASSET_SALE"
    elif sale and not rent:
        tx = "SALE"
        tx_rule = "V360_EXPLICIT_SALE"
    elif rent and not sale:
        tx = "RENT"
        tx_rule = "V360_EXPLICIT_RENT"
    elif sale and rent:
        # If both occur but are not proven separate sections, retain safe abstention.
        tx = "UNKNOWN"
        tx_rule = "V360_DUAL_TRANSACTION_ABSTAIN"
    else:
        tx = "UNKNOWN"
        tx_rule = "V360_TRANSACTION_ABSTAIN"

    if cls == "PROPERTY_AVAILABILITY" and (prop or len(ls) >= 4) and (sale or rent or money or portfolio_sale):
        own = "OWNED"
        own_rule = "V360_COMPLETE_AVAILABILITY_OWNED"
    elif cls == "REQUIREMENT" and req:
        own = "OWNED"
        own_rule = "V360_COMPLETE_REQUIREMENT_OWNED"
    elif cls in ("FRAGMENT", "UNRESOLVED"):
        own = "AMBIGUOUS"
        own_rule = "V360_INCOMPLETE_EVIDENCE_ABSTAIN"
    else:
        own = "AMBIGUOUS"
        own_rule = "V360_OWNERSHIP_ABSTAIN"

    # Confidence is evidence-derived, not a promotion claim.
    score = 72.0
    score += 8 if cls != "UNRESOLVED" else 0
    score += 7 if tx != "UNKNOWN" else 0
    score += 7 if own == "OWNED" else 0
    score += 3 if prop else 0
    score += 2 if contact else 0
    score = min(score, 99.0)
    return {"class":cls,"transaction":tx,"ownership":own,"confidence":score,
            "rule":"|".join((class_rule,tx_rule,own_rule))}


def _v1_rows(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text("""
          SELECT * FROM alliance_mastery_v340_blind_audit_cases
          WHERE audit_version=:v AND review_status='LABELED'
          ORDER BY created_at,audit_id
        """), {"v":EXAM_V1}).mappings().all()]


def _truth_hash(rows):
    payload=[]
    for r in rows:
        payload.append({
            "audit_id":str(r.get("audit_id")),
            "blind_id":str(r.get("blind_id")),
            "human_class":r.get("human_class"),
            "human_transaction":r.get("human_transaction"),
            "human_ownership":r.get("human_ownership"),
        })
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def freeze_exam_v1(engine):
    rows=_v1_rows(engine)
    if len(rows) < v340.AUDIT_TARGET:
        return {"status":"WAITING","labeled":len(rows),"target":v340.AUDIT_TARGET}
    truth_hash=_truth_hash(rows)
    comparable=correct=0
    for r in rows:
        for p,h in ((r.get("predicted_class"),r.get("human_class")),
                    (r.get("predicted_transaction"),r.get("human_transaction")),
                    (r.get("predicted_ownership"),r.get("human_ownership"))):
            if h:
                comparable += 1
                if p == h: correct += 1
    accuracy=round(100.0*correct/max(comparable,1),4) if comparable else None
    frozen={"exam_version":EXAM_V1,"labeled_cases":len(rows),"comparable_fields":comparable,
            "correct_fields":correct,"accuracy":accuracy,"truth_hash":truth_hash,
            "policy":"Immutable Exam V1 result. Never rescore original predictions after learning from these labels."}
    with engine.begin() as conn:
        existing=conn.execute(text("SELECT truth_hash FROM alliance_mastery_v360_exam_v1_freeze WHERE exam_version=:v"),{"v":EXAM_V1}).scalar()
        if existing and existing != truth_hash:
            raise RuntimeError("Frozen Exam V1 truth changed after freeze. Refusing to continue.")
        conn.execute(text("""
          INSERT INTO alliance_mastery_v360_exam_v1_freeze
          (freeze_id,exam_version,labeled_cases,comparable_fields,correct_fields,accuracy,truth_hash,frozen_result)
          VALUES(:id,:v,:lc,:cf,:ok,:acc,:h,CAST(:r AS jsonb))
          ON CONFLICT(exam_version) DO NOTHING
        """),{"id":str(uuid.uuid4()),"v":EXAM_V1,"lc":len(rows),"cf":comparable,"ok":correct,
              "acc":accuracy,"h":truth_hash,"r":_j(frozen)})
    return {"status":"FROZEN",**frozen}


def _lesson_for(field, pred, human, raw):
    n=_norm(raw)
    if field=="class":
        if human=="PROPERTY_AVAILABILITY" and re.search(r"\bideal\s+for\b",n):
            return "CLASS_IDEAL_FOR_NOT_REQUIREMENT", "'Ideal for' describes suitability/target buyer; it does not create a requirement when a specific property is offered."
        if human=="PROPERTY_AVAILABILITY":
            return "CLASS_COMPLETE_PROPERTY_BEATS_UNRESOLVED", "Specific property identity plus area/configuration and sale/rent/price evidence is property availability, not unresolved."
    if field=="transaction":
        if human=="SALE" and re.search(r"\b(?:pre[- ]?rented|rental\s+income)\b",n):
            return "TX_PRE_RENTED_ASSET_IS_SALE", "Pre-rented/rental-income inventory with asset prices is an investment SALE; rent is occupancy/income, not the offered transaction."
        if human=="UNKNOWN" and _mixed_explicit_sections(raw):
            return "TX_MIXED_SECTIONS_ABSTAIN", "A source containing separate explicit RENT and SALE sections must not collapse to one transaction at message level; abstain/UNKNOWN until atomic split."
        if human=="SALE":
            return "TX_EXPLICIT_SALE_PRIORITY", "Explicit 'for sale', 'out-right', sale inventory or asking sale price supports SALE."
        if human=="RENT":
            return "TX_EXPLICIT_RENT_PRIORITY", "Explicit 'for rent', 'to-let' or rental inventory supports RENT."
    if field=="ownership" and human=="OWNED":
        return "OWN_COMPLETE_RECORD", "A complete source-level availability with property identity and transaction evidence owns that message-level classification even when multiple atomic children require later splitting."
    return "GENERIC_AUDIT_FAILURE", "Preserve the independent human truth as a regression lesson; do not hard-code the audit ID."


def distill_lessons(engine):
    rows=_v1_rows(engine)
    created=0
    with engine.begin() as conn:
        for r in rows:
            pairs=(("class",r.get("predicted_class"),r.get("human_class")),
                   ("transaction",r.get("predicted_transaction"),r.get("human_transaction")),
                   ("ownership",r.get("predicted_ownership"),r.get("human_ownership")))
            for field,pred,human in pairs:
                if not human or pred==human: continue
                code,lesson=_lesson_for(field,pred,human,r.get("raw_text") or "")
                before=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v360_lessons
                  WHERE exam_version=:ev AND audit_id=:aid AND field_name=:f AND ruleset_version=:rv"""),
                  {"ev":EXAM_V1,"aid":str(r["audit_id"]),"f":field,"rv":RULESET_VERSION}).scalar() or 0
                conn.execute(text("""
                  INSERT INTO alliance_mastery_v360_lessons
                  (lesson_id,exam_version,audit_id,blind_id,field_name,predicted_value,human_value,
                   lesson_code,generalized_lesson,ruleset_version)
                  VALUES(:id,:ev,:aid,:bid,:f,:p,:h,:code,:lesson,:rv)
                  ON CONFLICT(exam_version,audit_id,field_name,ruleset_version) DO NOTHING
                """),{"id":str(uuid.uuid4()),"ev":EXAM_V1,"aid":str(r["audit_id"]),"bid":str(r["blind_id"]),
                      "f":field,"p":pred,"h":human,"code":code,"lesson":lesson,"rv":RULESET_VERSION})
                if not before: created += 1
    return {"status":"PASS","lessons_created":created,"failure_fields":sum(1 for r in rows for p,h in ((r.get('predicted_class'),r.get('human_class')),(r.get('predicted_transaction'),r.get('human_transaction')),(r.get('predicted_ownership'),r.get('human_ownership'))) if h and p!=h)}


def repair_benchmark(engine):
    rows=_v1_rows(engine)
    comparable=correct=0; field_tot=Counter(); field_ok=Counter(); errors=[]
    for r in rows:
        p=predict_message(r.get("raw_text") or "")
        pairs=(("class",p["class"],r.get("human_class")),
               ("transaction",p["transaction"],r.get("human_transaction")),
               ("ownership",p["ownership"],r.get("human_ownership")))
        for field,pv,hv in pairs:
            if not hv: continue
            comparable+=1;field_tot[field]+=1
            if pv==hv:
                correct+=1;field_ok[field]+=1
            else:
                errors.append({"audit_id":str(r["audit_id"]),"field":field,"human":hv,"predicted":pv,"raw_text":r.get("raw_text")})
    acc=round(100.0*correct/max(comparable,1),4) if comparable else None
    field_acc={k:round(100.0*field_ok[k]/max(field_tot[k],1),2) for k in field_tot}
    return {"cases":len(rows),"comparable_fields":comparable,"correct_fields":correct,"accuracy":acc,
            "field_accuracy":field_acc,"errors":errors,
            "note":"This is a post-learning repair regression on Exam V1 and is NOT an independent expertise score."}


def _candidate_pool(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT b.blind_id,b.raw_text
          FROM alliance_mastery_v330_blind_cases b
          WHERE b.blindset_version=:bv
            AND NOT EXISTS (
              SELECT 1 FROM alliance_mastery_v340_blind_audit_cases a
              WHERE a.blind_id=b.blind_id AND a.audit_version=:v1
            )
            AND NOT EXISTS (
              SELECT 1 FROM alliance_mastery_v360_exam_v2_cases x
              WHERE x.blind_id=b.blind_id AND x.exam_version=:v2
            )
          ORDER BY b.frozen_at,b.blind_id
        """),{"bv":v330.BLINDSET_VERSION,"v1":EXAM_V1,"v2":EXAM_V2}).mappings().all()]
    return rows


def _risk(raw, pred):
    n=_norm(raw); ls=_lines(raw); score=0; reasons=[]
    if pred["transaction"]=="UNKNOWN": score+=30; reasons.append("TRANSACTION_ABSTENTION")
    if pred["class"] in ("UNRESOLVED","FRAGMENT"): score+=30; reasons.append("CLASS_UNCERTAINTY")
    if _mixed_explicit_sections(raw): score+=35; reasons.append("MIXED_TRANSACTION_SECTIONS")
    if re.search(r"\b(?:pre[- ]?rented|rental\s+income)\b",n): score+=30; reasons.append("PRE_RENTED_SEMANTICS")
    if re.search(r"\bideal\s+for\b",n): score+=20; reasons.append("IDEAL_FOR_TRAP")
    if len(ls)>=10: score+=15; reasons.append("LONG_MULTI_LINE")
    if re.search(r"\b(?:inventory|phase|syds?|maint|price|demand)\b",n): score+=10; reasons.append("BOUNDARY_PATTERN")
    if re.search(r"\b(?:highway|airport|railway|km|away|sea view|field view)\b",n): score+=8; reasons.append("REFERENCE_CONTEXT")
    if pred["confidence"]<90: score+=15; reasons.append("LOW_CONFIDENCE")
    return score, reasons or ["DIVERSITY_SAMPLE"]


def seed_exam_v2(engine, target=EXAM_V2_TARGET):
    with engine.begin() as conn:
        existing=conn.execute(text("SELECT count(*) FROM alliance_mastery_v360_exam_v2_cases WHERE exam_version=:v"),{"v":EXAM_V2}).scalar() or 0
        if existing:
            return {"status":"ALREADY_FROZEN","total":int(existing),"target":target}
    rows=_candidate_pool(engine)
    ranked=[]
    for r in rows:
        p=predict_message(r.get("raw_text") or "")
        score,reasons=_risk(r.get("raw_text") or "",p)
        ranked.append((score,reasons,r,p))
    ranked.sort(key=lambda x:(-x[0],str(x[2]["blind_id"])))

    chosen=[]; signatures=Counter()
    for item in ranked:
        score,reasons,r,p=item
        sig=(p["class"],p["transaction"],p["ownership"],reasons[0])
        if signatures[sig]>=3: continue
        signatures[sig]+=1;chosen.append(item)
        if len(chosen)>=target: break
    if len(chosen)<target:
        used={str(x[2]["blind_id"]) for x in chosen}
        for item in ranked:
            if str(item[2]["blind_id"]) in used: continue
            chosen.append(item)
            if len(chosen)>=target: break

    with engine.begin() as conn:
        for score,reasons,r,p in chosen:
            conn.execute(text("""
              INSERT INTO alliance_mastery_v360_exam_v2_cases
              (audit_id,blind_id,exam_version,priority,reason,raw_text,predicted_class,
               predicted_transaction,predicted_ownership,prediction_confidence,prediction_rule)
              VALUES(:id,:bid,:ev,:priority,:reason,:raw,:pc,:pt,:po,:conf,:rule)
              ON CONFLICT(blind_id,exam_version) DO NOTHING
            """),{"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"ev":EXAM_V2,"priority":int(score),
                  "reason":",".join(reasons),"raw":r.get("raw_text") or "","pc":p["class"],
                  "pt":p["transaction"],"po":p["ownership"],"conf":p["confidence"],"rule":p["rule"]})
        total=conn.execute(text("SELECT count(*) FROM alliance_mastery_v360_exam_v2_cases WHERE exam_version=:v"),{"v":EXAM_V2}).scalar() or 0
    return {"status":"FROZEN","total":int(total),"target":target,
            "policy":"Predictions are frozen before any Exam V2 human truth is entered."}


def exam_v2_status(engine):
    with engine.connect() as conn:
        total=conn.execute(text("SELECT count(*) FROM alliance_mastery_v360_exam_v2_cases WHERE exam_version=:v"),{"v":EXAM_V2}).scalar() or 0
        labeled=conn.execute(text("SELECT count(*) FROM alliance_mastery_v360_exam_v2_cases WHERE exam_version=:v AND review_status='LABELED'"),{"v":EXAM_V2}).scalar() or 0
        rows=[dict(r) for r in conn.execute(text("SELECT * FROM alliance_mastery_v360_exam_v2_cases WHERE exam_version=:v AND review_status='LABELED'"),{"v":EXAM_V2}).mappings().all()]
    comparable=correct=0; ft=Counter(); fo=Counter()
    for r in rows:
        for f,p,h in (("class",r.get("predicted_class"),r.get("human_class")),
                      ("transaction",r.get("predicted_transaction"),r.get("human_transaction")),
                      ("ownership",r.get("predicted_ownership"),r.get("human_ownership"))):
            if h:
                comparable+=1;ft[f]+=1
                if p==h: correct+=1;fo[f]+=1
    acc=round(100.0*correct/max(comparable,1),2) if comparable else None
    field_acc={k:round(100.0*fo[k]/max(ft[k],1),2) for k in ft}
    return {"total":int(total),"labeled":int(labeled),"remaining":int(total-labeled),
            "comparable_fields":comparable,"correct_fields":correct,"accuracy":acc,"field_accuracy":field_acc}


def next_exam_v2(engine):
    with engine.connect() as conn:
        r=conn.execute(text("""
          SELECT audit_id,blind_id,priority,reason,raw_text,predicted_class,predicted_transaction,
                 predicted_ownership,prediction_confidence,prediction_rule
          FROM alliance_mastery_v360_exam_v2_cases
          WHERE exam_version=:v AND review_status='OPEN'
          ORDER BY priority DESC,frozen_at,audit_id LIMIT 1
        """),{"v":EXAM_V2}).mappings().first()
    if not r:
        return {"status":"COMPLETE"}
    d=dict(r); d["case_number"]=None
    st=exam_v2_status(engine); d["progress"]={"labeled":st["labeled"],"total":st["total"],"remaining":st["remaining"]}
    return foundation._json_safe(d)


def completed_exam_v2(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT audit_id,blind_id,human_class,human_transaction,human_ownership,human_confidence,
                 human_reason,updated_at
          FROM alliance_mastery_v360_exam_v2_cases
          WHERE exam_version=:v AND review_status='LABELED'
          ORDER BY updated_at DESC
        """),{"v":EXAM_V2}).mappings().all()]
    return foundation._json_safe(rows)


def save_exam_v2(engine,payload):
    allowed_class={"PROPERTY_AVAILABILITY","REQUIREMENT","INVENTORY_GROUP","FRAGMENT","NOISE","UNRESOLVED","AMBIGUOUS"}
    allowed_tx={"SALE","RENT","UNKNOWN","AMBIGUOUS"}
    allowed_own={"OWNED","NOT_OWNED","AMBIGUOUS"}
    hc=payload.get("human_class"); ht=payload.get("human_transaction"); ho=payload.get("human_ownership")
    if not any((hc,ht,ho)): raise ValueError("Choose at least one independently verifiable field.")
    if hc and hc not in allowed_class: raise ValueError("Invalid human_class")
    if ht and ht not in allowed_tx: raise ValueError("Invalid human_transaction")
    if ho and ho not in allowed_own: raise ValueError("Invalid human_ownership")
    aid=payload.get("audit_id")
    if not aid: raise ValueError("audit_id is required")
    with engine.begin() as conn:
        row=conn.execute(text("SELECT review_status FROM alliance_mastery_v360_exam_v2_cases WHERE audit_id=:id AND exam_version=:v FOR UPDATE"),{"id":aid,"v":EXAM_V2}).mappings().first()
        if not row: raise ValueError("Audit case not found")
        if row["review_status"]=="LABELED":
            return {"status":"ALREADY_LABELED","audit_id":aid,"progress":exam_v2_status(engine)}
        conn.execute(text("""
          UPDATE alliance_mastery_v360_exam_v2_cases
          SET human_class=:hc,human_transaction=:ht,human_ownership=:ho,human_confidence=:conf,
              human_reason=:reason,review_status='LABELED',updated_at=now()
          WHERE audit_id=:id AND exam_version=:v
        """),{"hc":hc,"ht":ht,"ho":ho,"conf":payload.get("human_confidence","HIGH"),
              "reason":payload.get("human_reason"),"id":aid,"v":EXAM_V2})
    st=exam_v2_status(engine)
    return {"status":"SAVED","audit_id":aid,"saved":{"class":hc,"transaction":ht,"ownership":ho},"progress":st}


def _expertise_gate(training, exam2):
    if not training.get("training_mastery_gate"):
        return "EXPERTISE_GATE_TRAINING_HOLD"
    if exam2["total"] < EXAM_V2_TARGET:
        return "EXPERTISE_GATE_EXAM_V2_NOT_FROZEN"
    if exam2["labeled"] < exam2["total"]:
        return "EXPERTISE_GATE_AWAITING_BLIND_EXAM_V2"
    acc=exam2.get("accuracy")
    critical=exam2.get("field_accuracy") or {}
    critical_ok=all(v>=EXPERTISE_CRITICAL_ACCURACY for k,v in critical.items() if k in ("transaction","ownership","class"))
    if acc is not None and acc>=EXPERTISE_FIELD_ACCURACY and critical_ok:
        return "EXPERTISE_V1_GATE_PASS"
    return "EXPERTISE_GATE_BLIND_EXAM_V2_HOLD"


def run(engine, limit=1000):
    _install(engine)
    training=v350.benchmark(v350._cases(engine))
    frozen=freeze_exam_v1(engine)
    lessons=distill_lessons(engine) if frozen.get("status")=="FROZEN" else {"status":"WAITING"}
    repair=repair_benchmark(engine) if frozen.get("status")=="FROZEN" else {"accuracy":None,"errors":[]}
    seed=seed_exam_v2(engine,EXAM_V2_TARGET) if frozen.get("status")=="FROZEN" else {"status":"WAITING","total":0}
    exam2=exam_v2_status(engine)
    gate=_expertise_gate(training,exam2)
    result={
        "status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
        "training_benchmark":training,"training_mastery_gate":"PASS" if training.get("training_mastery_gate") else "HOLD",
        "exam_v1_freeze":frozen,"lesson_distillation":lessons,"post_learning_exam_v1_regression":repair,
        "exam_v2_seed":seed,"exam_v2":exam2,"expertise_gate":gate,
        "policy":[
            "Exam V1 original score is immutable and never upgraded after learning.",
            "Exam V2 predictions are frozen before human labels.",
            "Exam V2 labels cannot modify predictions until the exam score is frozen.",
            "No production, WhatsApp, Gold V1 or Gold V2 writes."
        ],
        "next_step":"Complete only the frozen Exam V2 independent audit. The clearer UI shows SAVED confirmation and progress.",
        "production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0
    }
    with engine.begin() as conn:
        conn.execute(text("""
          INSERT INTO alliance_mastery_v360_runs
          (run_id,ruleset_version,training_accuracy,training_mastery_gate,exam_v1_accuracy,exam_v1_frozen,
           repair_accuracy,exam_v2_total,exam_v2_labeled,exam_v2_accuracy,expertise_gate,result,
           production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
          VALUES(:id,:rv,:ta,:tg,:e1,:f,:ra,:e2t,:e2l,:e2a,:eg,CAST(:r AS jsonb),0,0,0,0)
        """),{"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"ta":training.get("accuracy") or 0,
              "tg":bool(training.get("training_mastery_gate")),"e1":frozen.get("accuracy"),
              "f":frozen.get("status")=="FROZEN","ra":repair.get("accuracy"),"e2t":exam2["total"],
              "e2l":exam2["labeled"],"e2a":exam2.get("accuracy"),"eg":gate,"r":_j(result)})
    return foundation._json_safe(result)


def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("SELECT result FROM alliance_mastery_v360_runs WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1"),{"v":RULESET_VERSION}).scalar()
    return foundation._json_safe({"status":"PASS","version":VERSION,"latest_run":_loads(latest,{}) if latest else None,
        "exam_v2":exam_v2_status(engine),"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})

DASHBOARD = r"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Property Brain 3.6</title>
<style>
body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto;padding:0 12px}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:26px;font-weight:bold}button{padding:11px 16px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold;margin:4px;cursor:pointer}.sel{background:#9ee7a8}.muted{opacity:.75}pre{white-space:pre-wrap;overflow-wrap:anywhere}.saved{background:#123d2a;border:2px solid #56d68b}.warn{background:#3c3011;border:1px solid #d2ae43}.row{display:flex;gap:8px;flex-wrap:wrap}.pill{padding:7px 10px;border-radius:18px;background:#23384b}.complete{border-top:1px solid #345;padding:8px 0}textarea{width:100%;min-height:70px;background:#07111d;color:#eef6ff;border:1px solid #345;border-radius:8px;padding:8px}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}
</style></head><body>
<h1>🧠 Alliance Property Brain — Blind Failure Learning 3.6</h1>
<p>Exam V1 is frozen forever. 3.6 learns generalized lessons from it, then certifies only on a new unseen Exam V2 whose predictions were frozen before your answers.</p>
<button onclick='run360()'>Run 3.6 Learning + Freeze Exam V2</button>
<div id='cards' class='grid'></div><div id='saveBanner'></div>
<div class='card'><h2>Current Blind Exam V2 Case</h2><div id='case'></div></div>
<div class='card'><h3>Independent Human Truth</h3><p class='muted'>Choose only fields you can verify from the source. The machine prediction stays frozen and cannot be changed here.</p>
<div><b>Class:</b><div class='row' id='classBtns'></div></div><br><div><b>Transaction:</b><div class='row' id='txBtns'></div></div><br><div><b>Ownership:</b><div class='row' id='ownBtns'></div></div><br><textarea id='reason' placeholder='Short independent reason'></textarea><br><button id='saveBtn' onclick='saveCase()'>Save Blind Exam V2 Label</button></div>
<div class='card'><h3>Completed Exam V2 Cases</h3><div id='completed'></div></div>
<div class='card'><h3>Latest 3.6 Run</h3><pre id='latest'></pre></div>
<script>
let current=null, pick={human_class:null,human_transaction:null,human_ownership:null};
const classes=['PROPERTY_AVAILABILITY','REQUIREMENT','INVENTORY_GROUP','FRAGMENT','NOISE','UNRESOLVED','AMBIGUOUS'];
const txs=['SALE','RENT','UNKNOWN','AMBIGUOUS']; const owns=['OWNED','NOT_OWNED','AMBIGUOUS'];
async function call(p,m='GET',body=null){let o={method:m,headers:{}};if(body){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}let r=await fetch(p,o);let t=await r.text(),d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function card(k,v){return `<div class='card'><div>${k}</div><div class='big'>${v??0}</div></div>`}
function buttons(id,arr,key){let el=document.getElementById(id);el.innerHTML=arr.map(v=>`<button type='button' data-v='${v}' onclick="choose('${id}','${key}','${v}')">${v}</button>`).join('')}
function choose(id,key,v){pick[key]=v;document.querySelectorAll('#'+id+' button').forEach(b=>b.classList.toggle('sel',b.dataset.v===v))}
async function load(){let s=await call('/api/property-brain/mastery-v360/status');let l=s.latest_run||{};let e=s.exam_v2||{};document.getElementById('cards').innerHTML=card('3.5 Training',l.training_benchmark?.accuracy||'—')+card('Frozen Exam V1',l.exam_v1_freeze?.accuracy??'—')+card('Exam V2 Progress',(e.labeled||0)+'/'+(e.total||0))+card('Expertise Gate',l.expertise_gate||'NOT RUN');document.getElementById('latest').textContent=JSON.stringify(l,null,2);await loadCase();await loadCompleted()}
async function loadCase(){current=await call('/api/property-brain/mastery-v360/exam-v2/next');pick={human_class:null,human_transaction:null,human_ownership:null};buttons('classBtns',classes,'human_class');buttons('txBtns',txs,'human_transaction');buttons('ownBtns',owns,'human_ownership');document.getElementById('reason').value='';document.getElementById('saveBtn').disabled=false;if(current.status==='COMPLETE'){document.getElementById('case').innerHTML='<div class="saved"><h2>✅ EXAM V2 COMPLETE</h2><p>No unfinished blind cases remain.</p></div>';document.getElementById('saveBtn').disabled=true;return}let p=current.progress||{};document.getElementById('case').innerHTML=`<div class='row'><span class='pill'>Completed ${p.labeled||0}/${p.total||0}</span><span class='pill'>Remaining ${p.remaining||0}</span><span class='pill'>Priority ${current.priority}</span></div><h3>${current.reason}</h3><pre>${esc(current.raw_text)}</pre><div class='warn'><b>Frozen machine prediction</b><br>Class: ${current.predicted_class}<br>Transaction: ${current.predicted_transaction}<br>Ownership: ${current.predicted_ownership}<br>Confidence: ${current.prediction_confidence}</div>`}
function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function saveCase(){if(!current||current.status==='COMPLETE')return;document.getElementById('saveBtn').disabled=true;try{let d=await call('/api/property-brain/mastery-v360/exam-v2/label','POST',{audit_id:current.audit_id,...pick,human_confidence:'HIGH',human_reason:document.getElementById('reason').value});document.getElementById('saveBanner').innerHTML=`<div class='card saved'><h2>✅ SAVED</h2><b>Audit ID:</b> ${d.audit_id}<br><b>Saved:</b> ${JSON.stringify(d.saved)}<br><b>Progress:</b> ${d.progress.labeled}/${d.progress.total} completed, ${d.progress.remaining} remaining.</div>`;await loadCase();await loadCompleted();let s=await call('/api/property-brain/mastery-v360/status');let e=s.exam_v2||{};document.getElementById('cards').innerHTML=card('3.5 Training',s.latest_run?.training_benchmark?.accuracy||'—')+card('Frozen Exam V1',s.latest_run?.exam_v1_freeze?.accuracy??'—')+card('Exam V2 Progress',(e.labeled||0)+'/'+(e.total||0))+card('Expertise Gate',s.latest_run?.expertise_gate||'AWAITING RUN')}catch(e){document.getElementById('saveBanner').innerHTML=`<div class='card warn'>Save failed: ${esc(e.message)}</div>`;document.getElementById('saveBtn').disabled=false}}
async function loadCompleted(){let rows=await call('/api/property-brain/mastery-v360/exam-v2/completed');document.getElementById('completed').innerHTML=rows.length?rows.map(r=>`<div class='complete'>✅ ${r.audit_id}<br>${r.human_class||'—'} | ${r.human_transaction||'—'} | ${r.human_ownership||'—'}</div>`).join(''):'No completed Exam V2 cases yet.'}
async function run360(){await call('/api/property-brain/mastery-v360/run?limit=1000','POST');await load()}load();
</script></body></html>"""


def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    try: run(engine,1000)
    except Exception: pass

    if not foundation._route_exists(app,"/api/property-brain/mastery-v360/status"):
        @app.get("/api/property-brain/mastery-v360/status")
        def _status(): return status(engine)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v360/run"):
        @app.post("/api/property-brain/mastery-v360/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)): return run(engine,limit)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v360/exam-v2/next"):
        @app.get("/api/property-brain/mastery-v360/exam-v2/next")
        def _next(): return next_exam_v2(engine)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v360/exam-v2/completed"):
        @app.get("/api/property-brain/mastery-v360/exam-v2/completed")
        def _completed(): return completed_exam_v2(engine)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v360/exam-v2/label"):
        @app.post("/api/property-brain/mastery-v360/exam-v2/label")
        async def _label(request:Request):
            payload=await request.json()
            try: return save_exam_v2(engine,payload)
            except ValueError as exc:
                from fastapi import HTTPException
                raise HTTPException(status_code=400,detail=str(exc))

    if not foundation._route_exists(app,"/property-brain/mastery-v360"):
        @app.get("/property-brain/mastery-v360",response_class=HTMLResponse)
        def _dash(): return HTMLResponse(DASHBOARD)

    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/mastery-v360",
            "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
