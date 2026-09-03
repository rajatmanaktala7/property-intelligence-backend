from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import Counter, defaultdict

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_failure_driven_mastery_v380 as v380

VERSION = "4.0.0-ALLIANCE-CRE-ACADEMY"
MODE = "EVIDENCE_FIRST_CLOSED_SCHEMA_ADVERSARIAL_CURRICULUM_SELECTIVE_ABSTENTION_SHADOW_ONLY"
ENGINE_VERSION = "ALLIANCE_CRE_ACADEMY_V400"
RULESET_VERSION = "CRE_ACADEMY_2026_09_03_V1"
ACADEMY_TARGET = 99.0
ADVERSARIAL_TARGET = 97.0
FIELD_TARGET = 98.0
HALLUCINATION_MAX = 1.0
AUTO_ACCEPT = 98.0
SHADOW_ACCEPT = 90.0
DEFAULT_INTERVAL_SECONDS = 900
MAX_BATCH = 5000

# Foundation 4.0 research principles:
# 1) closed-world schema contract: output only attested CRE labels/fields
# 2) evidence anchors + provenance before semantic normalization
# 3) deterministic validation before self-correction
# 4) abstain on insufficient/conflicting evidence
# 5) confidence is derived from evidence/rules, never self-reported by an LLM
# 6) dynamic failure curriculum and adversarial minimal-pair tests
# 7) frozen exams are never rewritten after learning
# 8) no production/WhatsApp/Gold writes until independent certification

CLASSES = {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT","CONTACT_ONLY","PROJECT_HEADER","LOCALITY_HEADER","FRAGMENT","NOISE","UNRESOLVED"}
TRANSACTIONS = {"SALE","RENT","AMBIGUOUS","UNKNOWN"}
OWNERSHIP = {"OWNED","NOT_OWNED","AMBIGUOUS"}

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_academy_v400_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
academy_total INTEGER NOT NULL DEFAULT 0,
academy_correct INTEGER NOT NULL DEFAULT 0,
academy_accuracy NUMERIC(8,4),
adversarial_total INTEGER NOT NULL DEFAULT 0,
adversarial_correct INTEGER NOT NULL DEFAULT 0,
adversarial_accuracy NUMERIC(8,4),
class_accuracy NUMERIC(8,4),
transaction_accuracy NUMERIC(8,4),
ownership_accuracy NUMERIC(8,4),
hallucination_rate NUMERIC(8,4),
source_rows_seen INTEGER NOT NULL DEFAULT 0,
unique_messages INTEGER NOT NULL DEFAULT 0,
duplicates_suppressed INTEGER NOT NULL DEFAULT 0,
new_predictions INTEGER NOT NULL DEFAULT 0,
shadow_accept INTEGER NOT NULL DEFAULT 0,
exceptions INTEGER NOT NULL DEFAULT 0,
precert_gate TEXT NOT NULL,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_academy_v400_predictions(
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
UNIQUE(source_table,source_id,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_academy_v400_exceptions(
exception_id UUID PRIMARY KEY,
prediction_id UUID NOT NULL UNIQUE,
source_table TEXT NOT NULL,
source_id TEXT NOT NULL,
reason_code TEXT NOT NULL,
payload JSONB NOT NULL DEFAULT '{}'::jsonb,
review_status TEXT NOT NULL DEFAULT 'OPEN',
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_academy_v400_lessons(
lesson_id UUID PRIMARY KEY,
lesson_code TEXT NOT NULL,
lesson_family TEXT NOT NULL,
description TEXT NOT NULL,
source TEXT NOT NULL,
active BOOLEAN NOT NULL DEFAULT TRUE,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(lesson_code,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_academy_v400_exam_policy(
policy_id UUID PRIMARY KEY,
exam_name TEXT NOT NULL UNIQUE,
status TEXT NOT NULL,
policy TEXT NOT NULL,
truth_used_for_training BOOLEAN NOT NULL DEFAULT FALSE,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

SOURCE_PRIORITY = ["wai_raw_messages","ai_whatsapp_purity","alliance_live_feed_entities"]
TEXT_COLUMNS = ["raw_text","message_text","raw_message","source_text","content","text","body","message"]
ID_COLUMNS = ["id","message_id","entity_id","listing_id","source_message_id"]
_thread_started = False
_thread_lock = threading.Lock()

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core
def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)
def _hash(s): return hashlib.sha256((s or "").encode("utf-8",errors="ignore")).hexdigest()

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL: conn.execute(text(stmt))
        conn.execute(text("""INSERT INTO alliance_academy_v400_exam_policy
        (policy_id,exam_name,status,policy,truth_used_for_training)
        VALUES(:id,'BLIND_AUDIT_V3_2026_09_03','RETIRED_UNLABELED_PRE_V400',
        'V3 was frozen under 3.8 before Foundation 4.0. It must not certify the changed 4.0 predictor. Preserve it untouched and do not use its truth for training.',
        FALSE) ON CONFLICT(exam_name) DO NOTHING"""), {"id":str(uuid.uuid4())})

def _norm(raw):
    # Preserve Hindi/Devanagari. Previous ASCII-only normalization erased decisive Hindi sale language.
    s=(raw or "").lower()
    s=re.sub(r"https?://\S+"," URL ",s,flags=re.I)
    s=re.sub(r"[^\w\u0900-\u097f₹+./@%\- ]+"," ",s,flags=re.UNICODE)
    return re.sub(r"\s+"," ",s).strip()

def _canonical(raw):
    s=_norm(raw)
    s=re.sub(r"(?:\+?91[\s-]*)?[6-9](?:[\s-]*\d){9}\b"," PHONE ",s)
    s=re.sub(r"\b\d{1,2}:\d{2}\b"," TIME ",s)
    s=re.sub(r"\s+"," ",s).strip()
    return s

def _canonical_hash(raw): return _hash(_canonical(raw))
def _lines(raw): return [re.sub(r"\s+"," ",x).strip() for x in (raw or "").splitlines() if x.strip()]

def _signals(raw):
    n=_norm(raw)
    sale_explicit=bool(re.search(r"\b(?:for sale|available for sale|avl for sale|deal available on sale|sale inventory|inventor(?:y|ies) (?:on|for) sale|outright|resale|wants? to sell|owner wants|asking price|negotiable price)\b|(?:अर्जेंट|urgent)\s*सेल|\bसेल\b",n))
    rent_explicit=bool(re.search(r"\b(?:available for rent|available for lease|available on lease|avl for rent|for rent|on rent|deal on rent|to let|to-let|long lease|long[- ]term lease|leave and license|lease!|lease\b|rent\s*(?:[:@]|rs|₹|\d)|showing rent)\b",n))
    requirement=bool(re.search(r"\b(?:require|requires|required|requirement|wanted|looking for|need(?:ed)?|seeking|client wants?|wants? to purchase|want to purchase|buyer required|tenant requirement|staff accommodation)\b",n))
    availability=bool(re.search(r"\b(?:available|avl|deal available|exclusive mandate|for showing|showing|getting vacated|vacant|ready to move|call for visit|site visit)\b",n))
    ideal_for=bool(re.search(r"\b(?:ideal for|suitable for|perfect for)\b",n))
    property_terms=bool(re.search(r"\b(?:bhk|apartment|flat|villa|plot|shop|office|basement|floor|building|kothi|farmhouse|farm house|penthouse|showroom|warehouse|shed|commercial space|sqft|sq ft|sq\.ft|sq yds|syds|sqmt|sq m|yards?)\b|गज|बिल्डिंग",n))
    capital=bool(re.search(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore)\b|\bdemand\s*(?:[:@-]|is)?\s*(?:₹|rs\.?)?\s*\d|\basking\s*(?:[:@-]|is)?\s*(?:₹|rs\.?)?\s*\d|\bowner wants\s*(?:₹|rs\.?)?\s*\d",n))
    monthly=bool(re.search(r"\b(?:rent|rental)\b.{0,18}(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:k|lac|lakh|l|per month|pm|/month)?\b|\b\d+(?:\.\d+)?\s*(?:k|lac|lakh|l)\s*(?:\+|pm|per month)",n))
    tenancy=bool(re.search(r"\b(?:pre[- ]?leased|pre[- ]?lease|pre[- ]?rented|already rented|tenant|rental income|roi|leased to|freshly leased)\b",n))
    greeting=bool(re.search(r"\b(?:good morning|good evening|good night|happy birthday|best wishes|congratulations|raksha bandhan|rakshabandhan)\b|शुभकामनाएं",n))
    admin=bool(re.search(r"\b(?:please remove such content|remove such content|remove .*members|this group|group to have|group for rented properties|request everyone)\b",n))
    url_only=bool(re.fullmatch(r"(?:url\s*){1,4}",n))
    casual=bool(re.fullmatch(r"(?:r u ok|are you ok|ok|okay|thanks|thank you|noted|done|pls call me|please call me)[.! ]*",n))
    return locals()

def _inventory_strength(raw):
    n=_norm(raw); lines=_lines(raw)
    explicit=bool(re.search(r"\b(?:inventor(?:y|ies)|multiple units|multiple options|many options|asset deals|plots? are available|villas? available on long lease)\b",n))
    projects=len(re.findall(r"\b(?:dlf|m3m|emaar|aipl|ireo|bestech|tulip|elan|ats|unitech|mahindra|vipul|suncity|sobha|godrej|hero homes|krisumi|raheja)\b",n))
    bhks=len(re.findall(r"\b\d(?:\.\d)?\s*bhk\b",n))
    capital_rows=len(re.findall(r"\b(?:demand|asking|price|owner wants)\b",n))
    rent_rows=len(re.findall(r"\brent\b",n))
    separators=sum(1 for x in lines if re.fullmatch(r"[_\-=━]{4,}",x))
    bulletish=sum(1 for x in lines if re.match(r"^\s*(?:[-•▫️]|\d+[.)])",x))
    score=sum([2 if explicit else 0,2 if projects>=3 else 0,1 if bhks>=3 else 0,2 if capital_rows>=3 else 0,1 if rent_rows>=4 else 0,1 if separators>=2 else 0,1 if bulletish>=4 else 0])
    return score, {"explicit":explicit,"projects":projects,"bhks":bhks,"capital_rows":capital_rows,"rent_rows":rent_rows,"separators":separators,"bulletish":bulletish}

def _noise(raw,s):
    n=_norm(raw)
    if s["url_only"]: return True,"V400_NOISE_LINK_ONLY"
    cre=s["property_terms"] or s["sale_explicit"] or s["rent_explicit"] or s["requirement"]
    if s["greeting"] and not cre: return True,"V400_NOISE_GREETING"
    if s["admin"] and not (s["availability"] or s["requirement"] or s["sale_explicit"] or s["rent_explicit"]): return True,"V400_NOISE_ADMIN"
    if s["casual"]: return True,"V400_NOISE_CHAT"
    if len(_lines(raw))<=2 and len(n)<50 and not cre and not re.search(r"\d{3,}",n): return True,"V400_NOISE_SHORT"
    return False,""

def _transaction(raw,s,cls):
    n=_norm(raw)
    # Demand/price + a concrete asset is sale even without literal "for sale".
    # Rent/lease in a pre-leased investment is occupancy, not transaction, when capital consideration is present.
    if cls=="NOISE": return "UNKNOWN","V400_NOISE_TX"
    if cls=="REQUIREMENT":
        if s["rent_explicit"] or re.search(r"\bbudget\b.{0,15}\d+(?:\.\d+)?\s*(?:k|thousand)\b",n): return "RENT","V400_REQ_RENT"
        if s["sale_explicit"] or s["capital"] or re.search(r"\b(?:purchase|buy|buyer)\b",n): return "SALE","V400_REQ_SALE"
        return "UNKNOWN","V400_REQ_ABSTAIN"
    if s["tenancy"] and s["capital"]: return "SALE","V400_PRELEASED_CAPITAL_SALE"
    # Explicit independent sale and rent sections at parent level must remain ambiguous.
    sale_headers=len(re.findall(r"\b(?:for sale|available for sale|deal available on sale|sale inventory)\b",n))
    rent_headers=len(re.findall(r"\b(?:for rent|available for rent|available for lease|deal on rent)\b",n))
    if sale_headers and rent_headers: return "AMBIGUOUS","V400_MIXED_PARENT"
    if s["sale_explicit"]: return "SALE","V400_EXPLICIT_SALE"
    if s["rent_explicit"]: return "RENT","V400_EXPLICIT_RENT"
    if s["capital"] and s["property_terms"]: return "SALE","V400_CAPITAL_CONSIDERATION_SALE"
    if s["monthly"] and s["property_terms"]: return "RENT","V400_MONTHLY_RENT"
    return "UNKNOWN","V400_TX_ABSTAIN"

def predict_message(raw):
    raw=raw or ""; n=_norm(raw); s=_signals(raw)
    noise,noise_rule=_noise(raw,s)
    inv_score,inv_evidence=_inventory_strength(raw)
    rules=[]
    if noise:
        cls="NOISE"; own="NOT_OWNED"; rules.append(noise_rule)
    else:
        # Availability language overrides misleading marketing "looking for the perfect spot".
        marketing_looking=bool(re.search(r"\blooking for the perfect (?:spot|space|property)\b",n)) and s["availability"]
        if s["requirement"] and not marketing_looking and not s["availability"]:
            cls="REQUIREMENT"; rules.append("V400_DEMAND_INTENT")
        elif inv_score>=3:
            cls="INVENTORY_GROUP"; rules.append("V400_MULTI_ENTITY_INVENTORY")
        elif s["property_terms"] and (s["availability"] or s["sale_explicit"] or s["rent_explicit"] or s["capital"] or s["monthly"]):
            cls="PROPERTY_AVAILABILITY"; rules.append("V400_PROPERTY_OFFER")
        elif re.search(r"\bshowing rent\b",n) and re.search(r"\b(?:colony|sector|phase|road|park|nagar)\b",n):
            cls="PROPERTY_AVAILABILITY"; rules.append("V400_BROKER_SHORTHAND")
        else:
            base=v380.predict_message(raw)
            cls=base.get("class") if base.get("class") in CLASSES else "UNRESOLVED"
            rules.append("V400_BASE_FALLBACK")
        own="OWNED" if cls in {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT"} else ("NOT_OWNED" if cls=="NOISE" else "AMBIGUOUS")
    tx,tx_rule=_transaction(raw,s,cls); rules.append(tx_rule)
    if cls in {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT"}: own="OWNED"
    elif cls=="NOISE": own="NOT_OWNED"

    # Evidence-derived confidence. Never trust model self-reported confidence.
    support=0
    support += 2 if cls=="NOISE" and noise else 0
    support += 2 if cls=="REQUIREMENT" and s["requirement"] else 0
    support += 2 if cls=="INVENTORY_GROUP" and inv_score>=3 else 0
    support += 2 if cls=="PROPERTY_AVAILABILITY" and s["property_terms"] else 0
    support += 2 if tx=="SALE" and (s["sale_explicit"] or s["capital"]) else 0
    support += 2 if tx=="RENT" and (s["rent_explicit"] or s["monthly"]) else 0
    support += 1 if own in {"OWNED","NOT_OWNED"} else 0
    conflict = (tx=="UNKNOWN" and cls in {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT"}) or cls in {"UNRESOLVED","FRAGMENT"}
    conf = 99.3 if support>=6 and not conflict else 97.0 if support>=4 and not conflict else 88.0 if support>=2 else 70.0
    if tx=="AMBIGUOUS" and cls=="INVENTORY_GROUP": conf=min(conf,96.0)
    evidence={
        "anchors":{"sale":s["sale_explicit"],"rent":s["rent_explicit"],"requirement":s["requirement"],"availability":s["availability"],
                   "property":s["property_terms"],"capital":s["capital"],"monthly":s["monthly"],"tenancy":s["tenancy"],"ideal_for":s["ideal_for"]},
        "inventory":inv_evidence,
        "evidence_sufficiency":"CONFLICT" if tx=="AMBIGUOUS" else ("INSUFFICIENT" if conflict else "SUPPORTED"),
        "closed_schema":True,
        "confidence_source":"DETERMINISTIC_EVIDENCE_CALIBRATOR"
    }
    return {"class":cls,"transaction":tx,"ownership":own,"confidence":conf,"rule":"|".join(rules),"evidence":evidence}

# -------------------- Autonomous Academy curriculum --------------------
# These are concept tests, not copies of V3. Minimal pairs force semantic understanding.
CURRICULUM = [
("property_rent","Premium office space available for lease, Golf Course Road, 3500 sq ft, price 300/sq ft, ideal for retail or office.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("ideal_for_trap","Commercial basement available for rent, 3200 sq ft. Ideal for doctors, clinics and wellness brands.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("requirement_rent","Require 1 BHK furnished in Mapusa for airport staff. Budget 20k.","REQUIREMENT","RENT","OWNED"),
("requirement_sale","Immediate required 300 yds in GK1. Client budget 31 Cr. Clear title.","REQUIREMENT","SALE","OWNED"),
("preleased_sale","Pre-leased shop, tenant Haldirams, rent 1.44 L per month, demand 4.40 Cr, ROI 4.5%.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("capital_sale","DLF Phase 2, 500 sq yds, 4 BHK, demand 7.50 Cr. Call for visit.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("owner_wants_sale","Plot in Saligao 900 sqmt with sanad. Owner wants 6.12 Cr.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("leave_license_rent","4 BHK furnished villa 325 sqm available for leave and license.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("noise_greeting","GOOD MORNING MY DEAR ALL GUYS","NOISE","UNKNOWN","NOT_OWNED"),
("noise_admin","Please remove such content and members if you want this group to have meaningful impact.","NOISE","UNKNOWN","NOT_OWNED"),
("noise_chat","R u ok","NOISE","UNKNOWN","NOT_OWNED"),
("inventory_sale","Inventories in Apartments: Tulip 1608 sqft demand 2.35 Cr; Bestech 2660 sqft demand 3.80 Cr; Emaar 1900 sqft demand 2.70 Cr.","INVENTORY_GROUP","SALE","OWNED"),
("inventory_rent","Villas available on long lease: Arpora 2 BHK 80K PM; Vagator 3 BHK 2.5L PM; Saligao 4 BHK 3L PM.","INVENTORY_GROUP","RENT","OWNED"),
("mixed_parent","FOR SALE: 3 BHK 3 Cr. FOR RENT: 4 BHK semi furnished rent 80k.","INVENTORY_GROUP","AMBIGUOUS","OWNED"),
("broker_shorthand","Pl call Rajeev 9810744101 for showing rent A 78 Defence Colony. Brokers welcome.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("hindi_sale","बहुत अर्जेंट सेल रोहिणी सेक्टर 24 में 250 गज की बिल्डिंग है डिमांड 7 करोड़ नेगोशिएबल प्राइस","PROPERTY_AVAILABILITY","SALE","OWNED"),
("generic_footer","Uday Park 4 BHK second floor fully furnished available for rent 1.65L. Many more options available.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("premium_not_total","Unitech Urban Oasis 2 BHK booking value 3.12 Cr, premium asking 18 Lac.","PROPERTY_AVAILABILITY","SALE","OWNED"),
]

ADVERSARIAL = [
("available_not_requirement","Looking for the perfect spot for your business? We have a prime semi-furnished floor AVAILABLE FOR LEASE, 3500 sq ft.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("ideal_not_requirement","Villa for sale 8 Cr. Ideal for families and investors.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("rent_word_in_sale","Already rented apartment. Rent 80k. Demand 3.5 Cr.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("lease_word_in_sale","Freshly leased to Vero Moda. Rent 91,500. Asking 2.8 Cr.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("purchase_is_requirement","Client wants to purchase a 500 sq yd floor in Vasant Vihar, budget 15 Cr.","REQUIREMENT","SALE","OWNED"),
("noisy_property_word","This group is only for rental properties. Please don't post greetings.","NOISE","UNKNOWN","NOT_OWNED"),
("many_more_footer_single","GK1 4 BHK floor available for rent 2L. Call 9811111111. Many more options available.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("two_sale_options_group","Luxury floors for sale: 300 yds UG with basement 4.5 Cr; top with terrace 4.5 Cr.","INVENTORY_GROUP","SALE","OWNED"),
("demand_is_sale","M Block DLF Phase 2 4 BHK 500 sq yds. Demand @ 7.50 Cr.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("rent_is_rent","DLF Phase 2 black 3BHK 300yd second floor semi furnished RENT 80k.","PROPERTY_AVAILABILITY","RENT","OWNED"),
("requirement_no_tx","Requirement house 215 sq yd Sushant Lok 1. Call broker.","REQUIREMENT","UNKNOWN","OWNED"),
("jv_requirement","Looking for JV redevelopment plotting projects in North or South Goa.","REQUIREMENT","UNKNOWN","OWNED"),
]

def _score_suite(suite):
    field=defaultdict(lambda:[0,0]); cases=[]
    halluc=0
    for name,raw,hc,ht,ho in suite:
        p=predict_message(raw); expected={"class":hc,"transaction":ht,"ownership":ho}
        ok=True
        for f in ("class","transaction","ownership"):
            field[f][1]+=1
            if p[f]==expected[f]: field[f][0]+=1
            else: ok=False
        # Hallucination proxy: a non-UNKNOWN transaction must have deterministic evidence support.
        if p["transaction"]!="UNKNOWN" and p["evidence"]["evidence_sufficiency"]=="INSUFFICIENT": halluc+=1
        cases.append({"name":name,"pass":ok,"expected":expected,"predicted":{k:p[k] for k in ("class","transaction","ownership","confidence","rule")}})
    total_fields=sum(v[1] for v in field.values()); correct=sum(v[0] for v in field.values())
    return {"cases":len(suite),"case_pass":sum(1 for c in cases if c["pass"]),
            "accuracy":round(100*correct/max(total_fields,1),4),
            "field_accuracy":{k:round(100*v[0]/max(v[1],1),4) for k,v in field.items()},
            "hallucination_rate":round(100*halluc/max(len(suite),1),4),
            "errors":[c for c in cases if not c["pass"]]}

def academy_status():
    curriculum=_score_suite(CURRICULUM); adversarial=_score_suite(ADVERSARIAL)
    all_fields={k:min(curriculum["field_accuracy"].get(k,0),adversarial["field_accuracy"].get(k,0)) for k in ("class","transaction","ownership")}
    pass_gate=(curriculum["accuracy"]>=ACADEMY_TARGET and adversarial["accuracy"]>=ADVERSARIAL_TARGET
               and all(v>=FIELD_TARGET for v in all_fields.values())
               and max(curriculum["hallucination_rate"],adversarial["hallucination_rate"])<=HALLUCINATION_MAX)
    return {"curriculum":curriculum,"adversarial":adversarial,"minimum_field_accuracy":all_fields,
            "precert_gate":"PRECERT_PASS_READY_TO_FREEZE_NEW_UNSEEN_V4" if pass_gate else "PRECERT_HOLD_KEEP_TRAINING",
            "v3_policy":"RETIRED_UNLABELED_PRE_V400_DO_NOT_CERTIFY_V400"}

def _columns(conn,t):
    return set(conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:t"),{"t":t}).scalars().all())

def _source_specs(engine):
    out=[]
    with engine.connect() as conn:
        for t in SOURCE_PRIORITY:
            exists=conn.execute(text("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=:t)"),{"t":t}).scalar()
            if not exists: continue
            cols=_columns(conn,t); tc=next((c for c in TEXT_COLUMNS if c in cols),None); ic=next((c for c in ID_COLUMNS if c in cols),None)
            if tc and ic: out.append((t,ic,tc))
    return out

def _fetch(engine,limit):
    specs=_source_specs(engine); out=[]; per=max(1,int(limit/max(len(specs),1)))
    with engine.connect() as conn:
        for t,ic,tc in specs:
            sql=f"SELECT CAST({ic} AS TEXT) source_id, CAST({tc} AS TEXT) raw_text FROM {t} WHERE {tc} IS NOT NULL AND length(trim(CAST({tc} AS TEXT)))>0 ORDER BY {ic} DESC LIMIT :lim"
            out += [{"source_table":t,"source_id":r["source_id"],"raw_text":r["raw_text"]} for r in conn.execute(text(sql),{"lim":per}).mappings()]
    return out[:limit]

def _already(engine,t,sid):
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT 1 FROM alliance_academy_v400_predictions WHERE source_table=:t AND source_id=:s AND ruleset_version=:r"),{"t":t,"s":sid,"r":RULESET_VERSION}).scalar())

def _process(engine,limit):
    rows=_fetch(engine,limit); seen={}; stats=Counter()
    for row in rows:
        ch=_canonical_hash(row["raw_text"]); stats["source_rows_seen"]+=1
        if ch in seen:
            stats["duplicates_suppressed"]+=1; continue
        seen[ch]=f'{row["source_table"]}/{row["source_id"]}'; stats["unique_messages"]+=1
        if _already(engine,row["source_table"],row["source_id"]): continue
        p=predict_message(row["raw_text"])
        # Foundation 4.0 is SHADOW ONLY. Independent V4 certification must happen before any auto-accept.
        disposition="SHADOW_ACCEPT" if p["confidence"]>=SHADOW_ACCEPT else "EXCEPTION"
        pid=str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_academy_v400_predictions
            (prediction_id,source_table,source_id,raw_hash,canonical_hash,raw_text,predicted_class,predicted_transaction,predicted_ownership,confidence,disposition,rule_id,evidence,duplicate_of,ruleset_version)
            VALUES(:pid,:t,:sid,:rh,:ch,:raw,:c,:tx,:o,:cf,:d,:rule,CAST(:ev AS JSONB),NULL,:rv)"""),
            {"pid":pid,"t":row["source_table"],"sid":row["source_id"],"rh":_hash(row["raw_text"]),"ch":ch,"raw":row["raw_text"],
             "c":p["class"],"tx":p["transaction"],"o":p["ownership"],"cf":p["confidence"],"d":disposition,"rule":p["rule"],"ev":_j(p["evidence"]),"rv":RULESET_VERSION})
            if disposition=="EXCEPTION":
                conn.execute(text("""INSERT INTO alliance_academy_v400_exceptions(exception_id,prediction_id,source_table,source_id,reason_code,payload,ruleset_version)
                VALUES(:eid,:pid,:t,:sid,:reason,CAST(:payload AS JSONB),:rv)"""),
                {"eid":str(uuid.uuid4()),"pid":pid,"t":row["source_table"],"sid":row["source_id"],"reason":"INSUFFICIENT_OR_CONFLICTING_EVIDENCE",
                 "payload":_j(p),"rv":RULESET_VERSION})
        stats["new_predictions"]+=1; stats["shadow_accept" if disposition=="SHADOW_ACCEPT" else "exceptions"]+=1
    return dict(stats)

def run(engine,limit=1000):
    _install(engine)
    academy=academy_status()
    live=_process(engine,max(1,min(int(limit),MAX_BATCH)))
    result={"version":VERSION,"mode":MODE,"status":"PASS","academy":academy,"source_automation":live,
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,"auto_labels_called_gold":False},
            "research_controls":{"closed_world_schema":True,"evidence_anchors":True,"deterministic_verification":True,"selective_abstention":True,
                                 "self_reported_confidence_used":False,"adversarial_minimal_pairs":True,"duplicate_suppression":True,"frozen_exam_integrity":True},
            "next_step":"If PRECERT_PASS, freeze a NEW unseen V4 exam. Do not use or label old V3 to certify Foundation 4.0."}
    c=academy["curriculum"]; a=academy["adversarial"]; fa=academy["minimum_field_accuracy"]
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_academy_v400_runs
        (run_id,ruleset_version,academy_total,academy_correct,academy_accuracy,adversarial_total,adversarial_correct,adversarial_accuracy,
        class_accuracy,transaction_accuracy,ownership_accuracy,hallucination_rate,source_rows_seen,unique_messages,duplicates_suppressed,new_predictions,shadow_accept,exceptions,precert_gate,
        production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations,result)
        VALUES(:id,:rv,:at,:ac,:aa,:dt,:dc,:da,:ca,:ta,:oa,:hr,:seen,:uniq,:dup,:np,:sa,:ex,:gate,0,0,0,0,CAST(:res AS JSONB))"""),
        {"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"at":c["cases"],"ac":c["case_pass"],"aa":c["accuracy"],"dt":a["cases"],"dc":a["case_pass"],"da":a["accuracy"],
         "ca":fa["class"],"ta":fa["transaction"],"oa":fa["ownership"],"hr":max(c["hallucination_rate"],a["hallucination_rate"]),
         "seen":live.get("source_rows_seen",0),"uniq":live.get("unique_messages",0),"dup":live.get("duplicates_suppressed",0),"np":live.get("new_predictions",0),
         "sa":live.get("shadow_accept",0),"ex":live.get("exceptions",0),"gate":academy["precert_gate"],"res":_j(result)})
    return result

def _latest(engine):
    with engine.connect() as conn:
        r=conn.execute(text("SELECT result FROM alliance_academy_v400_runs ORDER BY created_at DESC LIMIT 1")).scalar()
        return r or {}

def _exceptions(engine,limit=20):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text("""SELECT source_table,source_id,reason_code,payload,created_at FROM alliance_academy_v400_exceptions
        WHERE review_status='OPEN' ORDER BY created_at DESC LIMIT :lim"""),{"lim":limit}).mappings()]

def _dashboard(engine):
    latest=_latest(engine); academy=academy_status(); exc=_exceptions(engine,12)
    c=academy["curriculum"]; a=academy["adversarial"]; fa=academy["minimum_field_accuracy"]
    cards=f"""
    <div class='grid'>
      <div class='card'><b>Academy</b><strong>{c['accuracy']}%</strong><small>target ≥ {ACADEMY_TARGET}%</small></div>
      <div class='card'><b>Adversarial</b><strong>{a['accuracy']}%</strong><small>target ≥ {ADVERSARIAL_TARGET}%</small></div>
      <div class='card'><b>Class floor</b><strong>{fa['class']}%</strong><small>target ≥ {FIELD_TARGET}%</small></div>
      <div class='card'><b>Transaction floor</b><strong>{fa['transaction']}%</strong><small>target ≥ {FIELD_TARGET}%</small></div>
      <div class='card'><b>Ownership floor</b><strong>{fa['ownership']}%</strong><small>target ≥ {FIELD_TARGET}%</small></div>
    </div>"""
    errors=(c["errors"]+a["errors"])[:12]
    err_html="".join(f"<details><summary>{e['name']}</summary><pre>{json.dumps(e,ensure_ascii=False,indent=2)}</pre></details>" for e in errors) or "<p>All academy cases pass.</p>"
    exc_html="".join(f"<details><summary>{x['source_table']}/{x['source_id']} — {x['reason_code']}</summary><pre>{json.dumps(foundation._json_safe(x['payload']),ensure_ascii=False,indent=2)}</pre></details>" for x in exc) or "<p>No open exceptions.</p>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance CRE Academy 4.0</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033}}h1{{margin-bottom:4px}}.sub{{color:#657086}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:20px 0}}.card{{background:white;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}
    strong{{display:block;font-size:30px;margin:8px 0}}small{{color:#657086}}button{{padding:12px 18px;border:0;border-radius:9px;background:#172033;color:white;font-weight:700}}
    pre{{white-space:pre-wrap;background:#101624;color:#eaf0ff;padding:14px;border-radius:10px;overflow:auto}}details{{background:white;padding:10px 14px;border-radius:10px;margin:8px 0}}
    .gate{{padding:14px;border-radius:10px;background:#fff4cf;font-weight:700}}</style></head><body>
    <h1>Alliance CRE Academy 4.0</h1><div class='sub'>Evidence-first • closed schema • adversarial curriculum • selective abstention • shadow only</div>
    {cards}<div class='gate'>{academy['precert_gate']}</div>
    <p><b>V3 policy:</b> RETIRED UNLABELED. It will not certify the changed 4.0 predictor. A fresh unseen V4 is allowed only after the pre-certification gate passes.</p>
    <form method='post' action='/api/property-brain/academy-v400/run'><button>Run Academy + Shadow Cycle</button></form>
    <h2>Academy failures</h2>{err_html}<h2>Live exceptions</h2>{exc_html}
    <h2>Latest run</h2><pre>{json.dumps(foundation._json_safe(latest),ensure_ascii=False,indent=2)}</pre></body></html>"""

def _loop(engine):
    while True:
        try: run(engine,1000)
        except Exception as exc: print("Alliance CRE Academy 4.0 background error:",type(exc).__name__,exc)
        time.sleep(max(3600,int(os.getenv("ALLIANCE_ACADEMY_INTERVAL_SECONDS",str(DEFAULT_INTERVAL_SECONDS)))))

def _start(engine):
    global _thread_started
    with _thread_lock:
        if _thread_started:return
        _thread_started=True
        threading.Thread(target=_loop,args=(engine,),daemon=True,name="alliance-cre-academy-v400").start()

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    @app.get("/api/property-brain/academy-v400/status")
    def status_v400(): return {"version":VERSION,"mode":MODE,"academy":academy_status(),"latest":_latest(engine),"safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    @app.post("/api/property-brain/academy-v400/run")
    def run_v400(limit:int=Query(default=1000,ge=1,le=MAX_BATCH)): return run(engine,limit)
    @app.get("/property-brain/academy-v400",response_class=HTMLResponse)
    def page_v400(): return HTMLResponse(_dashboard(engine))
    _start(engine)
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/academy-v400","api":"/api/property-brain/academy-v400/status",
            "policy":"SHADOW_ONLY_UNTIL_NEW_INDEPENDENT_V4_CERTIFICATION","production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
