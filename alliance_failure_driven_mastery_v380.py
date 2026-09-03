from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import Counter, defaultdict

from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_blind_failure_learning_v360 as v360
import alliance_ownership_mastery_blind_v330 as v330

VERSION = "3.8.0-FAILURE-DRIVEN-MASTERY-AUTOMATED"
MODE = "LEARN_FROZEN_V2_FAILURES_DEDUPE_ATOMIC_SHADOW_FREEZE_V3_NO_PRODUCTION_WRITES"
ENGINE_VERSION = "ALLIANCE_FAILURE_DRIVEN_MASTERY_V380"
RULESET_VERSION = "FAILURE_DRIVEN_MASTERY_2026_09_03_V1"
EXAM_V2 = v360.EXAM_V2
EXAM_V3 = "BLIND_AUDIT_V3_2026_09_03"
EXAM_V3_TARGET = 12
AUTO_ACCEPT = 98.0
SHADOW_ACCEPT = 90.0
DEFAULT_INTERVAL_SECONDS = 900
MAX_BATCH = 5000

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v380_exam_v2_freeze(
freeze_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
labeled_cases INTEGER NOT NULL,
comparable_fields INTEGER NOT NULL,
correct_fields INTEGER NOT NULL,
accuracy NUMERIC(8,4),
truth_hash TEXT NOT NULL,
frozen_result JSONB NOT NULL DEFAULT '{}'::jsonb,
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v380_lessons(
lesson_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL,
audit_id UUID NOT NULL,
field_name TEXT NOT NULL,
predicted_value TEXT,
human_value TEXT NOT NULL,
lesson_code TEXT NOT NULL,
generalized_lesson TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(exam_version,audit_id,field_name,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v380_predictions(
prediction_id UUID PRIMARY KEY,
source_table TEXT NOT NULL,
source_id TEXT NOT NULL,
raw_hash TEXT NOT NULL,
canonical_hash TEXT NOT NULL,
raw_text TEXT NOT NULL,
predicted_class TEXT NOT NULL,
predicted_transaction TEXT NOT NULL,
predicted_ownership TEXT NOT NULL,
confidence NUMERIC(6,2) NOT NULL,
disposition TEXT NOT NULL,
rule_id TEXT NOT NULL,
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
duplicate_of TEXT,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(source_table,source_id,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v380_exceptions(
exception_id UUID PRIMARY KEY,
prediction_id UUID NOT NULL UNIQUE,
source_table TEXT NOT NULL,
source_id TEXT NOT NULL,
reason_code TEXT NOT NULL,
payload JSONB NOT NULL DEFAULT '{}'::jsonb,
review_status TEXT NOT NULL DEFAULT 'OPEN',
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v380_exam_v3_cases(
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
human_reason TEXT,
review_status TEXT NOT NULL DEFAULT 'OPEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(blind_id,exam_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v380_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
exam_v2_accuracy NUMERIC(8,4),
post_learning_v2_accuracy NUMERIC(8,4),
source_rows_seen INTEGER NOT NULL DEFAULT 0,
unique_messages INTEGER NOT NULL DEFAULT 0,
duplicates_suppressed INTEGER NOT NULL DEFAULT 0,
new_predictions INTEGER NOT NULL DEFAULT 0,
auto_accept INTEGER NOT NULL DEFAULT 0,
shadow_accept INTEGER NOT NULL DEFAULT 0,
exceptions INTEGER NOT NULL DEFAULT 0,
exam_v3_total INTEGER NOT NULL DEFAULT 0,
exam_v3_labeled INTEGER NOT NULL DEFAULT 0,
exam_v3_accuracy NUMERIC(8,4),
expertise_gate TEXT NOT NULL,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

SOURCE_PRIORITY = ["wai_raw_messages", "ai_whatsapp_purity", "alliance_live_feed_entities"]
TEXT_COLUMNS = ["raw_text", "message_text", "raw_message", "source_text", "content", "text", "body", "message"]
ID_COLUMNS = ["id", "message_id", "entity_id", "listing_id", "source_message_id"]
_thread_started = False
_thread_lock = threading.Lock()


def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core
def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)
def _lines(raw): return [re.sub(r"\s+", " ", x).strip() for x in (raw or "").splitlines() if x.strip()]


def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))


def _norm(raw):
    s = (raw or "").lower()
    s = re.sub(r"https?://\S+", " URL ", s, flags=re.I)
    s = re.sub(r"[^a-z0-9₹+./@\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canonical(raw):
    s = _norm(raw)
    s = re.sub(r"\b(?:\+?91)?[6-9]\d{9}\b", " PHONE ", re.sub(r"[\s()-]", "", s))
    s = re.sub(r"\b(?:https?://)?\S+\.com\S*\b", " URL ", s)
    return re.sub(r"\s+", " ", s).strip()


def _hash(raw): return hashlib.sha256((raw or "").encode("utf-8", errors="ignore")).hexdigest()
def _canonical_hash(raw): return hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest()


def _has_phone(raw):
    compact = re.sub(r"[\s()-]", "", raw or "")
    return bool(re.search(r"(?<!\d)(?:\+?91)?[6-9]\d{9}(?!\d)", compact))


def _noise(raw):
    n = _norm(raw); lines = _lines(raw)
    urls = re.findall(r"https?://\S+", raw or "", re.I)
    no_urls = re.sub(r"https?://\S+", " ", raw or "", flags=re.I)
    no_urls = re.sub(r"[^A-Za-z0-9]+", " ", no_urls).strip()
    if urls and not no_urls: return True, "V380_NOISE_LINK_ONLY"
    cre = bool(re.search(r"\b(?:rent|sale|property|bhk|plot|floor|shop|office|villa|apartment|required|requirement|wanted|purchase|buy|lease|sqft|yard|syds)\b", n))
    if re.search(r"\b(?:good morning|good evening|good night|happy raksha|raksha bandhan|rakshabandhan|congratulations|best wishes)\b|शुभकामनाएं", raw or "", re.I) and not cre:
        return True, "V380_NOISE_GREETING"
    if re.search(r"\b(?:keep this group|request everyone|group for rented properties|group for rental properties)\b", n) and not re.search(r"\b(?:available|required|wanted|for sale|for rent)\b", n):
        return True, "V380_NOISE_ADMIN"
    conversational = bool(re.fullmatch(r"(?:r u ok|are you ok|i was trying (?:ur|your) no but could not|thanks|thank you|ok|okay|noted|done|pls call me|please call me)[.! ]*", n))
    if conversational: return True, "V380_NOISE_CONVERSATION"
    if len(lines) <= 2 and len(n) < 45 and not cre and not re.search(r"\b\d{2,}\b", n): return True, "V380_NOISE_SHORT_CHAT"
    return False, ""


def _requirement(raw):
    n = _norm(raw)
    return bool(re.search(r"\b(?:wanted|wants to purchase|want to purchase|wanted to purchase|purchase required|buyer required|immediate required|required|requirement|looking for|need(?:ed)?|seeking|urgent rental requirement|client budget|tenant meeting|client wants)\b", n))


def _rent(raw):
    n = _norm(raw)
    return bool(re.search(r"\b(?:available for rent|avail for rent|avl for rent|for rent|to let|to-let|wanted for rent|required on rent|rental requirement|asking rent|rent\s*[:@]|rent\s+\d|long[- ]term basis|staff accommodation)\b", n))


def _sale(raw):
    n = _norm(raw)
    # Deliberately excludes bare 'demand' and 'lease hold'. Both caused V2/V3-style false SALE/RENT signals.
    return bool(re.search(r"\b(?:for sale|avl for sale|available for sale|sale inventory|inventory for sale|asset deals? for sale|outright|out-right|resale|wants? to purchase|purchase|buyer|buying)\b", n))


def _budget_transaction(raw):
    n = _norm(raw)
    if _rent(raw): return "RENT"
    if _sale(raw): return "SALE"
    # Requirement amount magnitude: monthly-style values are likely rent; crore/lakh acquisition budgets are sale.
    if _requirement(raw):
        if re.search(r"\b(?:budget|within|upto|up to)\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?\s*(?:k|thousand)\b", n): return "RENT"
        if re.search(r"\b(?:budget|within|upto|up to)\s*(?:rs\.?|₹)?\s*\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh)\b", n): return "SALE"
    return "UNKNOWN"


def _pre_rented_sale(raw):
    n = _norm(raw)
    tenancy = bool(re.search(r"\b(?:pre[- ]?rented|pre[- ]?lease(?:d)?|rental income|tenant)\b", n))
    sale_context = bool(re.search(r"\b(?:for sale|asset deals? for sale|price\s*[:@-]?\s*(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:cr|crore))\b", n))
    return tenancy and sale_context


def _section_counts(raw):
    n = _norm(raw)
    rent_hits = len(re.findall(r"\b(?:for rent|rent\s*[:@]?\s*\d|rental inventory|avail(?:able)? for rent|avl for rent)\b", n))
    sale_hits = len(re.findall(r"\b(?:for sale|avl for sale|available for sale|resale|price\s*[:@]?\s*\d)\b", n))
    return sale_hits, rent_hits


def _mixed_sale_rent(raw):
    s, r = _section_counts(raw)
    return s > 0 and r > 0 and not _pre_rented_sale(raw)


def _dominant_transaction(raw):
    if _pre_rented_sale(raw): return "SALE", "V380_PRE_RENTED_ASSET_SALE"
    s, r = _section_counts(raw)
    n = _norm(raw)
    if s and r:
        # Large rental inventory may contain one resale-size summary. Preserve dominant explicit rent context.
        if r >= 5 and s <= 1 and re.search(r"\bnew floors? in resale\b", n): return "RENT", "V380_DOMINANT_RENT_WITH_RESale_SUMMARY"
        return "AMBIGUOUS", "V380_TRUE_MIXED_SALE_RENT_PARENT"
    if s: return "SALE", "V380_EXPLICIT_SALE"
    if r: return "RENT", "V380_EXPLICIT_RENT"
    return "UNKNOWN", "V380_TX_ABSTAIN"


def _property_identity(raw):
    n = _norm(raw)
    return bool(re.search(r"\b(?:\d+(?:\.\d+)?\s*(?:sq ?ft|sqft|sq ?yds?|syds?|sqm|sq ?m|yard|yards|mtr|meter)|\d+(?:\.\d+)?\s*bhk|\d+\s*\+\s*\d+|builder floor|apartment|flat|villa|farm house|farmhouse|basement|shop|office|plot|building|penthouse|house|kothi|show ?room|shed)\b", n))


def _specific_address(raw):
    n = _norm(raw)
    return bool(re.search(r"\b(?:[a-z]\s*[-/]?\s*\d{1,4}|sector\s*[- ]?\d+|phase\s*[- ]?\d+|block\s*[- ]?[a-z0-9]+)\b", n))


def _inventory_group(raw):
    n = _norm(raw); lines = _lines(raw)
    explicit = bool(re.search(r"\b(?:inventor(?:y|ies)|many options|multiple units|multiple inventories|asset deals|shops for sale and rent|pre[- ]?rent(?:ed)? asset deals|plots both options|plots are available)\b", n))
    item_numbers = len(re.findall(r"(?:^|\s)(?:\d{1,2})[.)]?\s+[a-z]", n))
    projects = len(re.findall(r"\b(?:sector[- ]?\d+[a-z]?|dlf phase\s*\d+|aipl joy|m3m |emaar |ireo |bestech |tulip |elan |hero homes|krisumi |ss linden|ats |pioneer |ridgewood|kalkaji|chitranjan park)\b", n))
    tx_mentions = len(re.findall(r"\b(?:for rent|for sale|rent\s*[:@]?\s*\d|price\s*[:@]?\s*\d|demand\s*[:@]?\s*\d)\b", n))
    if explicit and (tx_mentions >= 2 or projects >= 2): return True
    if item_numbers >= 3 and tx_mentions >= 2: return True
    if projects >= 3 and tx_mentions >= 3: return True
    if len(lines) >= 12 and tx_mentions >= 4: return True
    return False


def predict_message(raw):
    n = _norm(raw)
    noise, noise_rule = _noise(raw)
    req = _requirement(raw)
    group = _inventory_group(raw)
    prop = _property_identity(raw) or (_specific_address(raw) and (_rent(raw) or _sale(raw)))
    pre_sale = _pre_rented_sale(raw)
    mixed = _mixed_sale_rent(raw)
    rules = []

    if noise:
        return {"class":"NOISE","transaction":"UNKNOWN","ownership":"NOT_OWNED","confidence":99.5,
                "rule":noise_rule,"evidence":{"noise":True}}

    if req:
        cls = "REQUIREMENT"; rules.append("V380_REQUIREMENT_INTENT")
        tx = _budget_transaction(raw)
        if tx == "UNKNOWN":
            if re.search(r"\b(?:plot|plots|purchase|clear title|immediate payment)\b", n): tx = "SALE"; rules.append("V380_REQUIREMENT_PURCHASE_SEMANTICS")
            elif re.search(r"\b(?:staff accommodation|tenant|monthly|within\s*\d+\s*k)\b", n): tx = "RENT"; rules.append("V380_REQUIREMENT_RENT_SEMANTICS")
            else: rules.append("V380_REQUIREMENT_TX_ABSTAIN")
        else: rules.append("V380_REQUIREMENT_TX_EXPLICIT_OR_BUDGET")
        own = "OWNED"
    else:
        cls = "INVENTORY_GROUP" if group else ("PROPERTY_AVAILABILITY" if prop or _rent(raw) or _sale(raw) else "UNRESOLVED")
        rules.append("V380_INVENTORY_GROUP" if group else ("V380_PROPERTY_AVAILABILITY" if cls=="PROPERTY_AVAILABILITY" else "V380_CLASS_ABSTAIN"))
        tx, tx_rule = _dominant_transaction(raw); rules.append(tx_rule)
        # If no section-style signal exists, explicit single-message transaction still applies.
        if tx == "UNKNOWN":
            if _sale(raw): tx = "SALE"; rules.append("V380_SINGLE_SALE")
            elif _rent(raw): tx = "RENT"; rules.append("V380_SINGLE_RENT")
        own = "OWNED" if cls in ("PROPERTY_AVAILABILITY","INVENTORY_GROUP") else "AMBIGUOUS"

    if cls == "REQUIREMENT" and tx in ("SALE","RENT") and own == "OWNED": conf = 99.0
    elif cls == "INVENTORY_GROUP" and tx in ("SALE","RENT") and own == "OWNED": conf = 99.0
    elif cls == "INVENTORY_GROUP" and tx == "AMBIGUOUS" and own == "OWNED": conf = 97.0
    elif cls == "PROPERTY_AVAILABILITY" and tx in ("SALE","RENT") and own == "OWNED": conf = 99.0
    elif cls == "UNRESOLVED": conf = 70.0
    else: conf = 88.0

    return {"class":cls,"transaction":tx,"ownership":own,"confidence":conf,"rule":"|".join(rules),
            "evidence":{"requirement":req,"group":group,"property_identity":prop,"pre_rented_sale":pre_sale,
                        "mixed_sale_rent":mixed,"section_counts":_section_counts(raw),"phone":_has_phone(raw)}}


def _v2_rows(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text("SELECT * FROM alliance_mastery_v360_exam_v2_cases WHERE exam_version=:v AND review_status='LABELED' ORDER BY frozen_at,audit_id"), {"v":EXAM_V2}).mappings().all()]


def _truth_hash(rows):
    payload=[{"audit_id":str(r.get("audit_id")),"blind_id":str(r.get("blind_id")),"human_class":r.get("human_class"),"human_transaction":r.get("human_transaction"),"human_ownership":r.get("human_ownership")} for r in rows]
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def freeze_exam_v2(engine):
    rows=_v2_rows(engine)
    if len(rows) < v360.EXAM_V2_TARGET: return {"status":"WAITING","labeled":len(rows),"target":v360.EXAM_V2_TARGET}
    h=_truth_hash(rows); comp=ok=0; ft=Counter(); fo=Counter(); errors=[]
    for r in rows:
        for f,p,hv in (("class",r.get("predicted_class"),r.get("human_class")),("transaction",r.get("predicted_transaction"),r.get("human_transaction")),("ownership",r.get("predicted_ownership"),r.get("human_ownership"))):
            if not hv: continue
            comp+=1;ft[f]+=1
            if p==hv: ok+=1;fo[f]+=1
            else: errors.append({"audit_id":str(r["audit_id"]),"field":f,"predicted":p,"human":hv})
    acc=round(100*ok/max(comp,1),4); field={k:round(100*fo[k]/max(ft[k],1),2) for k in ft}
    frozen={"exam_version":EXAM_V2,"labeled_cases":len(rows),"comparable_fields":comp,"correct_fields":ok,"accuracy":acc,"field_accuracy":field,"truth_hash":h,"errors":errors,"policy":"Immutable independent Exam V2 score. Never rewritten after learning."}
    with engine.begin() as conn:
        prior=conn.execute(text("SELECT truth_hash FROM alliance_mastery_v380_exam_v2_freeze WHERE exam_version=:v"),{"v":EXAM_V2}).scalar()
        if prior and prior != h: raise RuntimeError("Frozen Exam V2 truth changed. Refusing to continue.")
        conn.execute(text("""INSERT INTO alliance_mastery_v380_exam_v2_freeze(freeze_id,exam_version,labeled_cases,comparable_fields,correct_fields,accuracy,truth_hash,frozen_result) VALUES(:id,:v,:lc,:cf,:ok,:a,:h,CAST(:r AS jsonb)) ON CONFLICT(exam_version) DO NOTHING"""),
                     {"id":str(uuid.uuid4()),"v":EXAM_V2,"lc":len(rows),"cf":comp,"ok":ok,"a":acc,"h":h,"r":_j(frozen)})
    return {"status":"FROZEN",**frozen}


def _lesson(field,human,raw):
    n=_norm(raw)
    if field=="class":
        if human=="NOISE": return "CLASS_CHAT_NOISE", "Greetings, admin notes, standalone links and casual chat are NOISE, not FRAGMENT."
        if human=="REQUIREMENT": return "CLASS_REQUIREMENT_DIRECTION", "Wanted/required/client-wants/purchase intent is demand-side REQUIREMENT even when a project/property type is named."
        if human=="INVENTORY_GROUP": return "CLASS_MULTI_PROPERTY_GROUP", "Multiple distinct offered properties/options in one message form an INVENTORY_GROUP before atomic child splitting."
        if human=="PROPERTY_AVAILABILITY": return "CLASS_SINGLE_OFFER", "One specific offered property with transaction evidence is PROPERTY_AVAILABILITY."
    if field=="transaction":
        if human=="SALE" and re.search(r"pre[- ]?rent|rental income|tenant",n): return "TX_PRE_RENTED_SALE", "Pre-rented income asset being offered with sale price/context is SALE; rent is occupancy/income."
        if human=="RENT": return "TX_RENT_DEMAND_NOT_SALE", "In rental listings, bare 'demand' is asking rent and must not create a SALE signal; leasehold is tenure, not lease transaction."
        if human=="AMBIGUOUS": return "TX_TRUE_MIXED_PARENT", "Parent source with explicit separate sale and rent inventory remains AMBIGUOUS until children are split."
        if human=="UNKNOWN": return "TX_SAFE_ABSTAIN", "When transaction is not source-supported, abstain rather than infer."
    if field=="ownership":
        if human=="OWNED": return "OWN_CRE_MESSAGE", "A complete property availability, inventory group or requirement owns its message-level intent."
        if human=="NOT_OWNED": return "OWN_NOISE_NOT_OWNED", "Noise/admin/chat does not own a property or requirement entity."
    return "GENERIC_V2_FAILURE", "Preserve this failure as a generalized regression lesson without hard-coding the audit ID."


def distill_v2_lessons(engine):
    rows=_v2_rows(engine); created=0; failures=0
    with engine.begin() as conn:
        for r in rows:
            for f,p,h in (("class",r.get("predicted_class"),r.get("human_class")),("transaction",r.get("predicted_transaction"),r.get("human_transaction")),("ownership",r.get("predicted_ownership"),r.get("human_ownership"))):
                if not h or p==h: continue
                failures+=1; code,lesson=_lesson(f,h,r.get("raw_text") or "")
                before=conn.execute(text("SELECT 1 FROM alliance_mastery_v380_lessons WHERE exam_version=:v AND audit_id=:a AND field_name=:f AND ruleset_version=:rv"),{"v":EXAM_V2,"a":str(r["audit_id"]),"f":f,"rv":RULESET_VERSION}).scalar()
                conn.execute(text("""INSERT INTO alliance_mastery_v380_lessons(lesson_id,exam_version,audit_id,field_name,predicted_value,human_value,lesson_code,generalized_lesson,ruleset_version) VALUES(:id,:v,:a,:f,:p,:h,:c,:l,:rv) ON CONFLICT(exam_version,audit_id,field_name,ruleset_version) DO NOTHING"""),
                             {"id":str(uuid.uuid4()),"v":EXAM_V2,"a":str(r["audit_id"]),"f":f,"p":p,"h":h,"c":code,"l":lesson,"rv":RULESET_VERSION})
                if not before: created+=1
    return {"status":"PASS","failure_fields":failures,"lessons_created":created}


def post_learning_v2_regression(engine):
    rows=_v2_rows(engine); comp=ok=0;ft=Counter();fo=Counter();errors=[]
    for r in rows:
        p=predict_message(r.get("raw_text") or "")
        for f,pv,hv in (("class",p["class"],r.get("human_class")),("transaction",p["transaction"],r.get("human_transaction")),("ownership",p["ownership"],r.get("human_ownership"))):
            if not hv: continue
            comp+=1;ft[f]+=1
            if pv==hv: ok+=1;fo[f]+=1
            else: errors.append({"audit_id":str(r["audit_id"]),"field":f,"predicted":pv,"human":hv,"raw_text":r.get("raw_text")})
    return {"accuracy":round(100*ok/max(comp,1),4),"correct_fields":ok,"comparable_fields":comp,
            "field_accuracy":{k:round(100*fo[k]/max(ft[k],1),2) for k in ft},"errors":errors,
            "note":"Diagnostic only. Exam V2 independent score remains frozen and immutable."}


def _columns(conn, table_name):
    return set(conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:t"),{"t":table_name}).scalars().all())


def _source_specs(engine):
    specs=[]
    with engine.connect() as conn:
        for t in SOURCE_PRIORITY:
            if not conn.execute(text("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=:t)"),{"t":t}).scalar(): continue
            cols=_columns(conn,t); txt=next((c for c in TEXT_COLUMNS if c in cols),None); ident=next((c for c in ID_COLUMNS if c in cols),None)
            if txt and ident: specs.append((t,ident,txt))
    return specs


def _fetch_candidates(engine,limit):
    out=[]; specs=_source_specs(engine); per=max(1,int(limit/max(len(specs),1)))
    with engine.connect() as conn:
        for t,i,c in specs:
            sql=f"SELECT CAST({i} AS TEXT) source_id, CAST({c} AS TEXT) raw_text FROM {t} WHERE {c} IS NOT NULL AND length(trim(CAST({c} AS TEXT)))>0 ORDER BY {i} DESC LIMIT :lim"
            for r in conn.execute(text(sql),{"lim":per}).mappings().all(): out.append({"source_table":t,"source_id":r["source_id"],"raw_text":r["raw_text"]})
    return out[:limit]


def _already(engine,t,sid):
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT 1 FROM alliance_mastery_v380_predictions WHERE source_table=:t AND source_id=:s AND ruleset_version=:r"),{"t":t,"s":sid,"r":RULESET_VERSION}).scalar())


def _first_by_canonical(engine,ch):
    with engine.connect() as conn:
        return conn.execute(text("SELECT source_table||'/'||source_id FROM alliance_mastery_v380_predictions WHERE canonical_hash=:h AND ruleset_version=:r ORDER BY created_at LIMIT 1"),{"h":ch,"r":RULESET_VERSION}).scalar()


def process_sources(engine,limit=500):
    rows=_fetch_candidates(engine,limit); new=auto=shadow=exc=dups=0; unique=0
    for r in rows:
        if _already(engine,r["source_table"],r["source_id"]): continue
        raw=r["raw_text"] or ""; rh=_hash(raw); ch=_canonical_hash(raw); duplicate_of=_first_by_canonical(engine,ch)
        if duplicate_of:
            p={"class":"NOISE","transaction":"UNKNOWN","ownership":"NOT_OWNED","confidence":99.9,"rule":"V380_DUPLICATE_SUPPRESSED","evidence":{"duplicate_of":duplicate_of}}
            disp="DUPLICATE_SUPPRESSED"; dups+=1
        else:
            p=predict_message(raw); unique+=1
            if p["confidence"]>=AUTO_ACCEPT and p["class"]!="UNRESOLVED" and p["transaction"]!="UNKNOWN": disp="SHADOW_HIGH_CONFIDENCE"; shadow+=1
            elif p["confidence"]>=SHADOW_ACCEPT: disp="SHADOW_ACCEPT"; shadow+=1
            else: disp="EXCEPTION"; exc+=1
        pid=str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_mastery_v380_predictions(prediction_id,source_table,source_id,raw_hash,canonical_hash,raw_text,predicted_class,predicted_transaction,predicted_ownership,confidence,disposition,rule_id,evidence,duplicate_of,ruleset_version) VALUES(:id,:t,:s,:rh,:ch,:raw,:pc,:pt,:po,:cf,:d,:rule,CAST(:e AS jsonb),:dup,:rv) ON CONFLICT(source_table,source_id,ruleset_version) DO NOTHING"""),
                         {"id":pid,"t":r["source_table"],"s":r["source_id"],"rh":rh,"ch":ch,"raw":raw,"pc":p["class"],"pt":p["transaction"],"po":p["ownership"],"cf":p["confidence"],"d":disp,"rule":p["rule"],"e":_j(p.get("evidence") or {}),"dup":duplicate_of,"rv":RULESET_VERSION})
            if disp=="EXCEPTION":
                conn.execute(text("""INSERT INTO alliance_mastery_v380_exceptions(exception_id,prediction_id,source_table,source_id,reason_code,payload,ruleset_version) VALUES(:id,:pid,:t,:s,:rc,CAST(:p AS jsonb),:rv) ON CONFLICT(prediction_id) DO NOTHING"""),
                             {"id":str(uuid.uuid4()),"pid":pid,"t":r["source_table"],"s":r["source_id"],"rc":p["rule"],"p":_j(p),"rv":RULESET_VERSION})
        new+=1
    return {"source_rows_seen":len(rows),"unique_messages":unique,"duplicates_suppressed":dups,"new_predictions":new,"auto_accept":auto,"shadow_accept":shadow,"exceptions":exc,
            "policy":"High-confidence output remains shadow until a NEW independent V3 certification passes. Routine labeling is automated."}


def _v3_candidate_pool(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text("""SELECT b.blind_id,b.raw_text FROM alliance_mastery_v330_blind_cases b WHERE b.blindset_version=:bv AND NOT EXISTS(SELECT 1 FROM alliance_mastery_v340_blind_audit_cases a WHERE a.blind_id=b.blind_id) AND NOT EXISTS(SELECT 1 FROM alliance_mastery_v360_exam_v2_cases x WHERE x.blind_id=b.blind_id) AND NOT EXISTS(SELECT 1 FROM alliance_mastery_v380_exam_v3_cases z WHERE z.blind_id=b.blind_id) ORDER BY b.frozen_at,b.blind_id"""),{"bv":v330.BLINDSET_VERSION}).mappings().all()]


def _risk(raw,p):
    score=0; reasons=[]; n=_norm(raw)
    if p["transaction"] in ("UNKNOWN","AMBIGUOUS"): score+=25;reasons.append("TX_COMPLEXITY")
    if p["class"] in ("UNRESOLVED","INVENTORY_GROUP"): score+=20;reasons.append("CLASS_COMPLEXITY")
    if _mixed_sale_rent(raw): score+=25;reasons.append("MIXED_TX")
    if re.search(r"\b(?:pre[- ]?rent|rental income|lease hold|demand|purchase|required|wanted)\b",n): score+=20;reasons.append("V2_FAILURE_PATTERN")
    if len(_lines(raw))>=10: score+=10;reasons.append("LONG_MESSAGE")
    return score,reasons or ["DIVERSITY"]


def seed_exam_v3(engine,target=EXAM_V3_TARGET):
    with engine.begin() as conn:
        existing=conn.execute(text("SELECT count(*) FROM alliance_mastery_v380_exam_v3_cases WHERE exam_version=:v"),{"v":EXAM_V3}).scalar() or 0
        if existing: return {"status":"ALREADY_FROZEN","total":int(existing),"target":target}
    ranked=[]
    for r in _v3_candidate_pool(engine):
        p=predict_message(r["raw_text"] or ""); score,reasons=_risk(r["raw_text"] or "",p);ranked.append((score,reasons,r,p))
    ranked.sort(key=lambda x:(-x[0],str(x[2]["blind_id"])))
    chosen=[]; sig=Counter()
    for item in ranked:
        _,reasons,_,p=item; k=(p["class"],p["transaction"],reasons[0])
        if sig[k]>=3: continue
        sig[k]+=1;chosen.append(item)
        if len(chosen)>=target: break
    if len(chosen)<target:
        used={str(x[2]["blind_id"]) for x in chosen}
        for item in ranked:
            if str(item[2]["blind_id"]) in used: continue
            chosen.append(item)
            if len(chosen)>=target: break
    with engine.begin() as conn:
        for score,reasons,r,p in chosen:
            conn.execute(text("""INSERT INTO alliance_mastery_v380_exam_v3_cases(audit_id,blind_id,exam_version,priority,reason,raw_text,predicted_class,predicted_transaction,predicted_ownership,prediction_confidence,prediction_rule) VALUES(:id,:bid,:v,:pr,:reason,:raw,:pc,:pt,:po,:cf,:rule) ON CONFLICT(blind_id,exam_version) DO NOTHING"""),
                         {"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"v":EXAM_V3,"pr":score,"reason":",".join(reasons),"raw":r["raw_text"],"pc":p["class"],"pt":p["transaction"],"po":p["ownership"],"cf":p["confidence"],"rule":p["rule"]})
        total=conn.execute(text("SELECT count(*) FROM alliance_mastery_v380_exam_v3_cases WHERE exam_version=:v"),{"v":EXAM_V3}).scalar() or 0
    return {"status":"FROZEN","total":int(total),"target":target,"policy":"Predictions frozen before independent V3 truth. V3 is certification only, not routine labeling."}


def exam_v3_status(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("SELECT * FROM alliance_mastery_v380_exam_v3_cases WHERE exam_version=:v AND review_status='LABELED'"),{"v":EXAM_V3}).mappings().all()]
        total=conn.execute(text("SELECT count(*) FROM alliance_mastery_v380_exam_v3_cases WHERE exam_version=:v"),{"v":EXAM_V3}).scalar() or 0
    comp=ok=0;ft=Counter();fo=Counter()
    for r in rows:
        for f,p,h in (("class",r.get("predicted_class"),r.get("human_class")),("transaction",r.get("predicted_transaction"),r.get("human_transaction")),("ownership",r.get("predicted_ownership"),r.get("human_ownership"))):
            if not h: continue
            comp+=1;ft[f]+=1
            if p==h: ok+=1;fo[f]+=1
    acc=round(100*ok/max(comp,1),2) if comp else None
    return {"total":int(total),"labeled":len(rows),"remaining":int(total-len(rows)),"accuracy":acc,"correct_fields":ok,"comparable_fields":comp,"field_accuracy":{k:round(100*fo[k]/max(ft[k],1),2) for k in ft}}


def _expertise_gate(v3):
    if v3["labeled"] < v3["total"] or v3["total"] < EXAM_V3_TARGET: return "EXPERTISE_GATE_AWAITING_INDEPENDENT_V3"
    crit=v3.get("field_accuracy") or {}; crit_ok=all(crit.get(k,0)>=90 for k in ("class","transaction","ownership"))
    if (v3.get("accuracy") or 0)>=95 and crit_ok: return "EXPERTISE_V1_GATE_PASS_ON_NEW_V3"
    return "EXPERTISE_GATE_V3_HOLD"


def next_v3(engine):
    with engine.connect() as conn:
        r=conn.execute(text("SELECT * FROM alliance_mastery_v380_exam_v3_cases WHERE exam_version=:v AND review_status='OPEN' ORDER BY priority DESC,frozen_at,audit_id LIMIT 1"),{"v":EXAM_V3}).mappings().first()
    if not r: return {"status":"COMPLETE","progress":exam_v3_status(engine)}
    d=dict(r); d["status"]="OPEN"; d["progress"]=exam_v3_status(engine); return foundation._json_safe(d)


def save_v3(engine,payload):
    aid=str(payload.get("audit_id") or "")
    allowed_c={"PROPERTY_AVAILABILITY","REQUIREMENT","INVENTORY_GROUP","FRAGMENT","NOISE","UNRESOLVED","AMBIGUOUS"}; allowed_t={"SALE","RENT","UNKNOWN","AMBIGUOUS"}; allowed_o={"OWNED","NOT_OWNED","AMBIGUOUS"}
    hc=payload.get("human_class"); ht=payload.get("human_transaction"); ho=payload.get("human_ownership")
    if hc not in allowed_c or ht not in allowed_t or ho not in allowed_o: raise ValueError("Choose valid class, transaction and ownership.")
    with engine.begin() as conn:
        row=conn.execute(text("SELECT review_status FROM alliance_mastery_v380_exam_v3_cases WHERE audit_id=:a AND exam_version=:v FOR UPDATE"),{"a":aid,"v":EXAM_V3}).mappings().first()
        if not row: raise ValueError("Unknown V3 audit id.")
        if row["review_status"]=="LABELED": raise ValueError("This frozen V3 case is already labeled.")
        conn.execute(text("UPDATE alliance_mastery_v380_exam_v3_cases SET human_class=:c,human_transaction=:t,human_ownership=:o,human_reason=:r,review_status='LABELED',updated_at=now() WHERE audit_id=:a"),{"c":hc,"t":ht,"o":ho,"r":payload.get("human_reason"),"a":aid})
    return {"status":"SAVED","audit_id":aid,"progress":exam_v3_status(engine)}


def run(engine,limit=500):
    _install(engine); frozen=freeze_exam_v2(engine)
    lessons=distill_v2_lessons(engine) if frozen.get("status")=="FROZEN" else {"status":"WAITING"}
    repair=post_learning_v2_regression(engine) if frozen.get("status")=="FROZEN" else {"accuracy":None,"errors":[]}
    source=process_sources(engine,limit); v3seed=seed_exam_v3(engine) if frozen.get("status")=="FROZEN" else {"status":"WAITING","total":0}; v3=exam_v3_status(engine); gate=_expertise_gate(v3)
    result={"status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,"exam_v2_freeze":frozen,"v2_lessons":lessons,"post_learning_v2_regression":repair,"source_automation":source,"exam_v3_seed":v3seed,"exam_v3":v3,"expertise_gate":gate,
            "automation":{"routine_labeling":"AUTOMATED","duplicate_suppression":"AUTOMATED","failure_learning":"AUTOMATED","human_work":"ONLY_PERIODIC_INDEPENDENT_CERTIFICATION_OR_TRUE_EXCEPTIONS"},
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,"auto_labels_called_gold":False},
            "next_step":"Use routine automation now. V3 is a small independent certification set only; do not use it for training until its score is frozen."}
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_mastery_v380_runs(run_id,ruleset_version,exam_v2_accuracy,post_learning_v2_accuracy,source_rows_seen,unique_messages,duplicates_suppressed,new_predictions,auto_accept,shadow_accept,exceptions,exam_v3_total,exam_v3_labeled,exam_v3_accuracy,expertise_gate,result) VALUES(:id,:rv,:e2,:pl,:seen,:uniq,:dup,:new,:aa,:sh,:ex,:v3t,:v3l,:v3a,:g,CAST(:r AS jsonb))"""),
                     {"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"e2":frozen.get("accuracy"),"pl":repair.get("accuracy"),"seen":source["source_rows_seen"],"uniq":source["unique_messages"],"dup":source["duplicates_suppressed"],"new":source["new_predictions"],"aa":source["auto_accept"],"sh":source["shadow_accept"],"ex":source["exceptions"],"v3t":v3["total"],"v3l":v3["labeled"],"v3a":v3.get("accuracy"),"g":gate,"r":_j(result)})
    return foundation._json_safe(result)


def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("SELECT result FROM alliance_mastery_v380_runs ORDER BY created_at DESC LIMIT 1")).scalar()
        open_exc=conn.execute(text("SELECT count(*) FROM alliance_mastery_v380_exceptions WHERE review_status='OPEN'")).scalar() or 0
        shadow=conn.execute(text("SELECT count(*) FROM alliance_mastery_v380_predictions WHERE disposition LIKE 'SHADOW%' AND ruleset_version=:r"),{"r":RULESET_VERSION}).scalar() or 0
        dups=conn.execute(text("SELECT count(*) FROM alliance_mastery_v380_predictions WHERE disposition='DUPLICATE_SUPPRESSED' AND ruleset_version=:r"),{"r":RULESET_VERSION}).scalar() or 0
    return foundation._json_safe({"status":"PASS","version":VERSION,"latest_run":json.loads(latest) if isinstance(latest,str) else (latest or {}),"exam_v3":exam_v3_status(engine),"open_exceptions":int(open_exc),"shadow":int(shadow),"duplicates_suppressed":int(dups),"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})


def exceptions(engine,limit=50):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""SELECT e.exception_id,e.source_table,e.source_id,e.reason_code,p.predicted_class,p.predicted_transaction,p.predicted_ownership,p.confidence,p.raw_text FROM alliance_mastery_v380_exceptions e JOIN alliance_mastery_v380_predictions p ON p.prediction_id=e.prediction_id WHERE e.review_status='OPEN' ORDER BY e.created_at DESC LIMIT :lim"""),{"lim":limit}).mappings().all()]
    return foundation._json_safe(rows)

DASHBOARD = r"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Property Brain 3.8</title><style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto;padding:0 12px}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:26px;font-weight:bold}button{padding:11px 16px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold;margin:4px;cursor:pointer}.muted{opacity:.75}pre{white-space:pre-wrap;overflow-wrap:anywhere}.good{background:#123d2a}.warn{background:#3c3011}.row{display:flex;gap:8px;flex-wrap:wrap}.pill{padding:7px 10px;border-radius:18px;background:#23384b}textarea{width:100%;min-height:60px;background:#07111d;color:#eef6ff;border:1px solid #345;border-radius:8px;padding:8px}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}</style></head><body><h1>🧠 Alliance Property Brain — Failure-Driven Mastery 3.8</h1><p>Routine training is automated. V2 stays frozen forever. 3.8 learns only after freeze, suppresses duplicate messages, repairs transaction/class semantics and keeps all output shadow until a new independent V3 certification passes.</p><button onclick='run()'>Run 3.8 Automation Now</button><div id='cards' class='grid'></div><div class='card'><h3>Operating Policy</h3><b>Routine messages:</b> automatic. <b>Duplicates:</b> suppressed. <b>Gold/Production/WhatsApp:</b> untouched.<br><b>V3:</b> certification only, not routine labeling.</div><div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div><div class='card'><h3>Open Exceptions</h3><div id='exceptions'></div></div><div class='card'><h3>Independent V3 Certification</h3><div id='v3'></div><div id='v3form'><div class='row' id='cb'></div><div class='row' id='tb'></div><div class='row' id='ob'></div><textarea id='reason' placeholder='Optional independent reason'></textarea><button onclick='saveV3()'>Save V3 Label</button></div></div><script>let cur=null,pick={};const C=['PROPERTY_AVAILABILITY','REQUIREMENT','INVENTORY_GROUP','FRAGMENT','NOISE','UNRESOLVED','AMBIGUOUS'],T=['SALE','RENT','UNKNOWN','AMBIGUOUS'],O=['OWNED','NOT_OWNED','AMBIGUOUS'];async function call(p,m='GET',b=null){let o={method:m,headers:{}};if(b){o.headers['Content-Type']='application/json';o.body=JSON.stringify(b)}let r=await fetch(p,o),t=await r.text(),d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}function card(k,v){return `<div class='card'><div>${k}</div><div class='big'>${v??'—'}</div></div>`}function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}function btns(id,arr,key){document.getElementById(id).innerHTML=arr.map(v=>`<button onclick="pick['${key}']='${v}'">${v}</button>`).join('')}async function load(){let s=await call('/api/property-brain/mastery-v380/status'),l=s.latest_run||{},v=s.exam_v3||{};document.getElementById('cards').innerHTML=card('Frozen Exam V2',l.exam_v2_freeze?.accuracy)+card('Post-Learning V2',l.post_learning_v2_regression?.accuracy)+card('Duplicates Suppressed',s.duplicates_suppressed)+card('V3 Progress',(v.labeled||0)+'/'+(v.total||0));document.getElementById('latest').textContent=JSON.stringify(l,null,2);let ex=await call('/api/property-brain/mastery-v380/exceptions?limit=25');document.getElementById('exceptions').innerHTML=ex.length?ex.map(x=>`<div class='card warn'><b>${x.source_table}/${x.source_id}</b><br>${x.predicted_class} | ${x.predicted_transaction} | ${x.predicted_ownership} | ${x.confidence}<pre>${esc(x.raw_text)}</pre></div>`).join(''):'No open exceptions.';await loadV3()}async function loadV3(){cur=await call('/api/property-brain/mastery-v380/exam-v3/next');btns('cb',C,'human_class');btns('tb',T,'human_transaction');btns('ob',O,'human_ownership');if(cur.status==='COMPLETE'){document.getElementById('v3').innerHTML='<div class="good card">V3 certification set complete.</div>';document.getElementById('v3form').style.display='none';return}document.getElementById('v3form').style.display='block';document.getElementById('v3').innerHTML=`<div class='pill'>${cur.progress.labeled}/${cur.progress.total} completed</div><pre>${esc(cur.raw_text)}</pre><div class='muted'>Machine prediction is intentionally hidden from the independent reviewer until scoring.</div>`}async function saveV3(){if(!cur||cur.status==='COMPLETE')return;if(!pick.human_class||!pick.human_transaction||!pick.human_ownership){alert('Choose all 3 fields');return}await call('/api/property-brain/mastery-v380/exam-v3/label','POST',{audit_id:cur.audit_id,...pick,human_reason:document.getElementById('reason').value});pick={};document.getElementById('reason').value='';await load()}async function run(){await call('/api/property-brain/mastery-v380/run?limit=500','POST');await load()}load();</script></body></html>"""


def _loop(engine):
    while True:
        try: run(engine,int(os.getenv("ALLIANCE_V380_BATCH","500")))
        except Exception: pass
        time.sleep(max(300,int(os.getenv("ALLIANCE_V380_INTERVAL_SECONDS",str(DEFAULT_INTERVAL_SECONDS)))))


def _start_thread(engine):
    global _thread_started
    with _thread_lock:
        if _thread_started: return
        _thread_started=True
        threading.Thread(target=_loop,args=(engine,),daemon=True,name="alliance-v380-teacher").start()


def register(core):
    engine=_engine(core);app=_app(core);_install(engine)
    try: run(engine,500)
    except Exception: pass
    _start_thread(engine)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v380/status"):
        @app.get("/api/property-brain/mastery-v380/status")
        def _status(): return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v380/run"):
        @app.post("/api/property-brain/mastery-v380/run")
        def _run(limit:int=Query(default=500,ge=1,le=MAX_BATCH)): return run(engine,limit)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v380/exceptions"):
        @app.get("/api/property-brain/mastery-v380/exceptions")
        def _exceptions(limit:int=Query(default=50,ge=1,le=500)): return exceptions(engine,limit)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v380/exam-v3/next"):
        @app.get("/api/property-brain/mastery-v380/exam-v3/next")
        def _next(): return next_v3(engine)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v380/exam-v3/label"):
        @app.post("/api/property-brain/mastery-v380/exam-v3/label")
        async def _label(request:Request):
            from fastapi import HTTPException
            try: return save_v3(engine,await request.json())
            except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))
    if not foundation._route_exists(app,"/property-brain/mastery-v380"):
        @app.get("/property-brain/mastery-v380",response_class=HTMLResponse)
        def _dash(): return HTMLResponse(DASHBOARD)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/mastery-v380","automation":"ROUTINE_LABELING_AUTOMATED","production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
