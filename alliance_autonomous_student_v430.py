from __future__ import annotations

import hashlib
import html
import inspect
import json
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v400 as v400
import alliance_cre_academy_v401 as v401
import alliance_cre_academy_v402 as v402
import alliance_cre_championship_v410 as v410
import alliance_automation_truth_escalator_v421 as v421
import alliance_automation_closure_v422 as v422
import alliance_automation_grammar_rescue_v423 as v423
import alliance_acquisition_intent_closure_v425 as v425

VERSION = "4.3.0-ALLIANCE-AUTONOMOUS-STUDENT-V5"
MODE = "LEARN_FROM_CLOSED_V4_FAILURES_REGRESSION_GATE_FREEZE_FRESH_V5_AUTO_EXAM_NO_PRODUCTION_WRITES"
RULESET_VERSION = "CRE_STUDENT_2026_09_03_V430"
V4_EXAM_VERSION = v410.EXAM_VERSION
V5_EXAM_VERSION = "BLIND_AUDIT_V5_2026_09_03"
V5_TARGET = 20
OVERALL_PASS = 95.0
FIELD_PASS = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_student_v430_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
v4_training_accuracy NUMERIC(8,4),
v4_class_accuracy NUMERIC(8,4),
v4_transaction_accuracy NUMERIC(8,4),
v4_ownership_accuracy NUMERIC(8,4),
legacy_regression_accuracy NUMERIC(8,4),
lesson_regression_accuracy NUMERIC(8,4),
training_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_manifest(
manifest_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
target INTEGER NOT NULL,
predictor_version TEXT NOT NULL,
predictor_sha256 TEXT NOT NULL,
selection_policy TEXT NOT NULL,
case_manifest_hash TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'FROZEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_cases(
audit_id UUID PRIMARY KEY,
blind_id UUID NOT NULL UNIQUE,
exam_version TEXT NOT NULL,
ordinal INTEGER NOT NULL,
source_hash TEXT NOT NULL,
raw_text TEXT NOT NULL,
predicted_class TEXT NOT NULL,
predicted_transaction TEXT NOT NULL,
predicted_ownership TEXT NOT NULL,
prediction_confidence NUMERIC(6,2),
prediction_rule TEXT,
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(exam_version,ordinal))""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_truth(
truth_id UUID PRIMARY KEY,
audit_id UUID NOT NULL UNIQUE,
exam_version TEXT NOT NULL,
truth_class TEXT,
truth_transaction TEXT,
truth_ownership TEXT,
class_confidence NUMERIC(6,4),
transaction_confidence NUMERIC(6,4),
ownership_confidence NUMERIC(6,4),
consensus JSONB NOT NULL DEFAULT '{}'::jsonb,
status TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_results(
result_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
total_cases INTEGER NOT NULL,
auto_resolved INTEGER NOT NULL,
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

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _j(v):
    return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _norm(raw):
    return v400._norm(raw or "")

def _lines(raw):
    return v400._lines(raw or "")

# ---------------------------------------------------------------------------
# Student 4.3
# Learn concepts from V4 failures, never memorize V4 source hashes/text.
# Ownership from 4.0.2 is intentionally preserved unless class semantics require
# the same already-established OWNED/NOT_OWNED mapping.
# ---------------------------------------------------------------------------

def _semantic_features(raw):
    n = _norm(raw)
    lines = _lines(raw)

    strong_requirement = bool(re.search(
        r"\b(?:immediate(?:ly)?\s+required|required|requirement|wanted|need(?:ed)?|seeking|"
        r"wants?\s+to\s+(?:purchase|buy|acquire)|looking\s+to\s+(?:purchase|buy|acquire)|"
        r"buyer\s+(?:required|requires?|wants?|seeks?)|tenant\s+requirement)\b", n))

    soft_requirement = bool(re.search(r"\b(?:looking for|client wants?)\b", n))

    availability = bool(re.search(
        r"\b(?:available|avl|for\s+sale|for\s+rent|available\s+for\s+lease|available\s+on\s+lease|"
        r"to[- ]?let|getting\s+vacated|gets\s+vacated|vacated|vacant|ready\s+to\s+move|"
        r"for\s+showing|showing|asking\s+rent|asking\s+price|owner\s+wants\s+to\s+sell|"
        r"deal\s+available|exclusive\s+mandate)\b", n))

    asset = bool(re.search(
        r"\b(?:bhk|flats?|apartments?|villas?|plots?|shops?|offices?|basements?|floors?|"
        r"buildings?|kothis?|farmhouses?|farm\s+houses?|penthouses?|showrooms?|warehouses?|"
        r"commercial\s+spaces?|retail\s+spaces?|units?|land|sqft|sq\s*ft|sq\s*yds?|syds?|"
        r"yards?|gaj|sqm|sqmt|acre)\b|गज|बिल्डिंग", n))

    purchase = bool(re.search(
        r"\b(?:purchase|buy|buyer|acquire|acquisition|outright|for\s+sale|resale|sale\s+inventory|"
        r"sell(?:ing)?|owner\s+wants\s+to\s+sell)\b", n))

    rent = bool(re.search(
        r"\b(?:for\s+rent|on\s+rent|rent(?:al)?|lease|to[- ]?let|tenant\s+requirement)\b", n))

    capital = bool(re.search(
        r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore)\b|"
        r"\b(?:demand|asking|price|budget)\b.{0,35}(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:cr|crore)\b", n))

    monthly = bool(re.search(
        r"\brent(?:al)?\b.{0,30}(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:k|lac|lakh|l)?\b|"
        r"\b\d+(?:\.\d+)?\s*(?:k|lac|lakh|l)\s*(?:pm|per\s+month|/month)\b", n))

    tenancy = bool(re.search(
        r"\b(?:pre[- ]?leased|pre[- ]?rented|freshly\s+leased|leased\s+to|tenant|rental\s+income|roi)\b", n))

    sale_headers = len(re.findall(r"\b(?:for\s+sale|available\s+for\s+sale|sale\s+inventory)\b", n))
    rent_headers = len(re.findall(r"\b(?:for\s+rent|available\s+for\s+rent|available\s+for\s+lease)\b", n))
    prices = len(re.findall(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh|l)\b", n))
    bhks = len(re.findall(r"\b\d+(?:\.\d+)?\s*bhk\b", n))
    option_terms = len(re.findall(r"\b(?:upper\s+ground|ug|ground|first|second|third|top|terrace|basement)\b", n))
    project_hits = len(re.findall(
        r"\b(?:dlf|m3m|emaar|aipl|ireo|bestech|tulip|elan|ats|unitech|mahindra|vipul|"
        r"suncity|sobha|godrej|hero\s+homes|krisumi|raheja)\b", n))
    bulletish = sum(1 for x in lines if re.match(r"^\s*(?:[-•▫️]|\d+[.)])", x))
    explicit_group = bool(re.search(
        r"\b(?:inventory|inventories|multiple\s+options|multiple\s+units|many\s+options|"
        r"asset\s+deals|options\s+available|following\s+properties)\b", n))

    group = (
        (sale_headers > 0 and rent_headers > 0)
        or explicit_group
        or (prices >= 2 and (bhks >= 2 or option_terms >= 2))
        or (project_hits >= 3 and prices >= 2)
        or (bulletish >= 3 and prices >= 2)
    )

    greeting = bool(re.search(
        r"\b(?:good\s+morning|good\s+evening|good\s+night|best\s+wishes|happy\s+birthday|"
        r"raksha\s+bandhan|rakshabandhan|congratulations)\b|शुभकामनाएं", n))
    admin = bool(re.search(
        r"\b(?:this\s+group|group\s+for\s+rented\s+properties|request\s+everyone|"
        r"remove\s+such\s+content|please\s+don'?t\s+post)\b", n))
    url_only = bool(re.fullmatch(r"(?:url\s*){1,6}", n))

    return {
        "n": n,
        "strong_requirement": strong_requirement,
        "soft_requirement": soft_requirement,
        "availability": availability,
        "asset": asset,
        "purchase": purchase,
        "rent": rent,
        "capital": capital,
        "monthly": monthly,
        "tenancy": tenancy,
        "group": group,
        "sale_headers": sale_headers,
        "rent_headers": rent_headers,
        "prices": prices,
        "bhks": bhks,
        "option_terms": option_terms,
        "project_hits": project_hits,
        "bulletish": bulletish,
        "explicit_group": explicit_group,
        "greeting": greeting,
        "admin": admin,
        "url_only": url_only,
    }

def predict_message(raw):
    raw = raw or ""
    base = v402.predict_message(raw)
    f = _semantic_features(raw)
    rules = [base.get("rule", "V402_BASE")]
    evidence = dict(base.get("evidence") or {})

    cls = base["class"]
    tx = base["transaction"]
    own = base["ownership"]
    conf = float(base["confidence"])

    # Noise semantics remain conservative and do not depend on the V4 failure set.
    if (f["greeting"] or f["admin"] or f["url_only"]) and not (
        f["asset"] or f["strong_requirement"] or f["availability"] or f["purchase"] or f["rent"]
    ):
        cls, tx, own = "NOISE", "UNKNOWN", "NOT_OWNED"
        conf = max(conf, 99.2)
        rules.append("V430_NOISE_SEMANTIC_GUARD")
    else:
        # Multi-entity evidence outranks single-property classification.
        if f["group"]:
            cls = "INVENTORY_GROUP"
            own = "OWNED"
            rules.append("V430_MULTI_ENTITY_GROUP_REPAIR")
            conf = max(conf, 98.8)

        # Strong buyer/tenant intent is a requirement only when no offer/availability
        # language owns the clause. This directly repairs the Requirement-vs-Property family.
        elif f["strong_requirement"] and not f["availability"]:
            cls = "REQUIREMENT"
            own = "OWNED"
            rules.append("V430_STRONG_DEMAND_INTENT")
            conf = max(conf, 98.9)

        # Availability/offer semantics outrank soft phrases such as "client wants"
        # or "looking for", which often appear inside broker property descriptions.
        elif f["availability"] and (f["asset"] or f["rent"] or f["purchase"] or f["capital"] or f["monthly"]):
            cls = "PROPERTY_AVAILABILITY"
            own = "OWNED"
            rules.append("V430_AVAILABILITY_OUTRANKS_SOFT_DEMAND")
            conf = max(conf, 98.9)

        # Economic property identity: periodic rent/tenancy + asset context is still
        # a property offer even when brokers omit "available".
        elif f["asset"] and (f["rent"] or f["purchase"] or f["capital"] or f["monthly"] or f["tenancy"]):
            if not f["strong_requirement"]:
                cls = "PROPERTY_AVAILABILITY"
                own = "OWNED"
                rules.append("V430_ECONOMIC_PROPERTY_IDENTITY")
                conf = max(conf, 98.5)

    # Transaction repair, separated from occupancy.
    if cls == "NOISE":
        tx = "UNKNOWN"
    elif cls == "REQUIREMENT":
        if f["purchase"] or (f["capital"] and not f["rent"]):
            tx = "SALE"
            rules.append("V430_REQUIREMENT_ACQUISITION_SALE")
        elif f["rent"] or f["monthly"]:
            tx = "RENT"
            rules.append("V430_REQUIREMENT_RENT")
        else:
            tx = "UNKNOWN"
            rules.append("V430_REQUIREMENT_ABSTAIN")
    elif cls == "INVENTORY_GROUP" and f["sale_headers"] and f["rent_headers"]:
        tx = "AMBIGUOUS"
        rules.append("V430_MIXED_GROUP_AMBIGUOUS")
    elif f["tenancy"] and f["capital"]:
        tx = "SALE"
        rules.append("V430_PRELEASED_CAPITAL_SALE")
    elif f["purchase"] or (f["capital"] and not f["rent"]):
        tx = "SALE"
        rules.append("V430_PROPERTY_SALE")
    elif f["rent"] or f["monthly"]:
        tx = "RENT"
        rules.append("V430_PROPERTY_RENT")

    # Preserve mature ownership semantics.
    if cls in {"PROPERTY_AVAILABILITY", "INVENTORY_GROUP", "REQUIREMENT"}:
        own = "OWNED"
    elif cls == "NOISE":
        own = "NOT_OWNED"

    evidence["v430_features"] = {k:v for k,v in f.items() if k != "n"}
    evidence["v430_policy"] = "Generic V4 failure-family repair; no V4 text/hash lookup."
    return {
        "class": cls,
        "transaction": tx,
        "ownership": own,
        "confidence": round(conf, 2),
        "rule": "|".join(x for x in rules if x),
        "evidence": evidence,
    }

# ---------------------------------------------------------------------------
# V4 closed-exam truth reader. V4 is training data only after its certification
# was concluded as HOLD. It is never used to certify 4.3.
# ---------------------------------------------------------------------------

def _v4_truth_rows(engine):
    v425.run(engine)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
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
        """), {"e": V4_EXAM_VERSION}).mappings()]
    out = []
    for x in rows:
        truth = None
        source = None
        if x["a_status"] == "AUTO_RESOLVED":
            truth = (x["a_class"], x["a_tx"], x["a_own"]); source = "V421"
        elif x["z_status"] == "AUTO_RESOLVED":
            truth = (x["z_class"], x["z_tx"], x["z_own"]); source = "V422"
        elif x["r_status"] == "AUTO_RESOLVED":
            truth = (x["r_class"], x["r_tx"], x["r_own"]); source = "V423"
        elif x["q_status"] == "AUTO_RESOLVED":
            truth = (x["q_class"], x["q_tx"], x["q_own"]); source = "V425"
        if truth:
            out.append({"row": x, "truth": truth, "truth_source": source})
    return out

def _score_cases(cases):
    fs = {f:[0,0] for f in ("class","transaction","ownership")}
    errors = []
    case_ok = 0
    for name, raw, hc, ht, ho in cases:
        p = predict_message(raw)
        exp = {"class":hc,"transaction":ht,"ownership":ho}
        ok = True
        for f in fs:
            fs[f][1] += 1
            if p[f] == exp[f]:
                fs[f][0] += 1
            else:
                ok = False
                errors.append({"name":name,"field":f,"truth":exp[f],"student":p[f]})
        case_ok += int(ok)
    total = sum(v[1] for v in fs.values())
    correct = sum(v[0] for v in fs.values())
    return {
        "cases": len(cases),
        "accuracy": round(100*correct/max(total,1),4),
        "field_accuracy": {k:round(100*v[0]/max(v[1],1),4) for k,v in fs.items()},
        "case_accuracy": round(100*case_ok/max(len(cases),1),4),
        "errors": errors,
    }

LESSON_REGRESSION = [
    ("availability_with_client_budget",
     "Defence Colony 2BHK fully furnished getting vacated next month. Rent up to Rs 1.50 lakh as per client budget.",
     "PROPERTY_AVAILABILITY","RENT","OWNED"),
    ("property_rent_with_soft_looking",
     "Looking for a good tenant. First floor flat is available for rent, 3 BHK, asking rent 1.25 lakh.",
     "PROPERTY_AVAILABILITY","RENT","OWNED"),
    ("purchase_requirement_plural_assets",
     "Buyer wants to purchase freehold plots in sectors 29 and 35. Direct owner deal required.",
     "REQUIREMENT","SALE","OWNED"),
    ("rental_requirement",
     "Immediate requirement for a furnished 3 BHK on rent in South Delhi. Budget 1 lakh.",
     "REQUIREMENT","RENT","OWNED"),
    ("sale_offer_with_client_phrase",
     "Client wants quick closure. 4 BHK apartment available for sale, asking 8.5 Cr.",
     "PROPERTY_AVAILABILITY","SALE","OWNED"),
    ("multi_sale_inventory",
     "Inventory for sale: DLF 3 BHK 4.2 Cr; Emaar 4 BHK 5.1 Cr; M3M 3 BHK 3.8 Cr.",
     "INVENTORY_GROUP","SALE","OWNED"),
    ("mixed_parent_inventory",
     "AVAILABLE FOR SALE: 3 BHK 3.2 Cr, 4 BHK 4.1 Cr. AVAILABLE FOR RENT: 3 BHK 75k, 4 BHK 90k.",
     "INVENTORY_GROUP","AMBIGUOUS","OWNED"),
    ("preleased_sale",
     "Pre-leased retail shop. Tenant national bank. Rent 1.1 lakh per month. Asking 3.1 Cr.",
     "PROPERTY_AVAILABILITY","SALE","OWNED"),
]

def training_status(engine):
    v4 = _v4_truth_rows(engine)
    v4_cases = []
    for item in v4:
        x = item["row"]
        hc,ht,ho = item["truth"]
        v4_cases.append((f"V4_{x['ordinal']}", x["raw_text"], hc, ht, ho))
    v4_score = _score_cases(v4_cases)

    legacy = []
    seen = set()
    for suite in (v400.CURRICULUM, v400.ADVERSARIAL, v401.REPAIR_REGRESSION, v402.REPAIR_REGRESSION):
        for row in suite:
            key = (row[0], row[1], row[2], row[3], row[4])
            if key not in seen:
                seen.add(key)
                legacy.append(row)
    legacy_score = _score_cases(legacy)
    lesson_score = _score_cases(LESSON_REGRESSION)

    v4_complete = len(v4_cases) == 20
    v4_ok = (
        v4_complete
        and v4_score["accuracy"] >= 98.0
        and all(v >= 95.0 for v in v4_score["field_accuracy"].values())
    )
    legacy_ok = (
        legacy_score["accuracy"] >= 99.0
        and all(v >= 98.0 for v in legacy_score["field_accuracy"].values())
    )
    lesson_ok = (
        lesson_score["accuracy"] == 100.0
        and all(v == 100.0 for v in lesson_score["field_accuracy"].values())
    )
    passed = v4_ok and legacy_ok and lesson_ok

    return {
        "version": VERSION,
        "v4_training": v4_score,
        "legacy_regression": legacy_score,
        "lesson_regression": lesson_score,
        "v4_truth_cases": len(v4_cases),
        "training_gate": "V430_TRAINING_PASS_READY_FOR_FRESH_V5" if passed else "V430_TRAINING_HOLD_DO_NOT_FREEZE_V5",
        "scientific_policy": "V4 is closed training data after HOLD. V5 must contain only untouched blind IDs and is the only certification set for Student 4.3.",
        "safety": {"production_writes":0,"whatsapp_writes":0,"gold_mutations":0},
    }

def _record_training(engine, s):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_student_v430_runs
        (run_id,ruleset_version,v4_training_accuracy,v4_class_accuracy,v4_transaction_accuracy,
         v4_ownership_accuracy,legacy_regression_accuracy,lesson_regression_accuracy,training_gate,result)
        VALUES(:id,:rv,:va,:vc,:vt,:vo,:la,:lr,:gate,CAST(:res AS JSONB))"""),
        {
            "id": str(uuid.uuid4()),
            "rv": RULESET_VERSION,
            "va": s["v4_training"]["accuracy"],
            "vc": s["v4_training"]["field_accuracy"]["class"],
            "vt": s["v4_training"]["field_accuracy"]["transaction"],
            "vo": s["v4_training"]["field_accuracy"]["ownership"],
            "la": s["legacy_regression"]["accuracy"],
            "lr": s["lesson_regression"]["accuracy"],
            "gate": s["training_gate"],
            "res": _j(s),
        })

# ---------------------------------------------------------------------------
# Fresh V5 selection. Excludes V1/V2/V3/V4 and any already-frozen V5 cases.
# ---------------------------------------------------------------------------

def _table_exists(conn, table):
    return bool(conn.execute(text("""SELECT EXISTS(
      SELECT 1 FROM information_schema.tables
      WHERE table_schema=current_schema() AND table_name=:t)"""), {"t":table}).scalar())

def _column_exists(conn, table, col):
    return bool(conn.execute(text("""SELECT EXISTS(
      SELECT 1 FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name=:t AND column_name=:c)"""),
      {"t":table,"c":col}).scalar())

def _used_blind_ids(conn):
    used = set()
    candidates = [
        "alliance_mastery_v340_blind_audit_cases",
        "alliance_mastery_v360_exam_v2_cases",
        "alliance_mastery_v380_exam_v3_cases",
        "alliance_championship_v410_cases",
        "alliance_v5_cases",
    ]
    for t in candidates:
        if _table_exists(conn,t) and _column_exists(conn,t,"blind_id"):
            try:
                used.update(str(x) for x in conn.execute(text(
                    f"SELECT blind_id FROM {t} WHERE blind_id IS NOT NULL"
                )).scalars().all())
            except Exception:
                pass
    return used

def _candidate_pool(engine):
    with engine.connect() as conn:
        if not _table_exists(conn,"alliance_mastery_v330_blind_cases"):
            raise RuntimeError("Foundation 3.3 blind pool table is missing.")
        rows = [dict(r) for r in conn.execute(text("""
          SELECT blind_id,source_hash,raw_text,frozen_at,status
          FROM alliance_mastery_v330_blind_cases
          WHERE status='FROZEN'
          ORDER BY blind_id
        """)).mappings()]
        used = _used_blind_ids(conn)
    return [r for r in rows if str(r["blind_id"]) not in used]

def _risk_bucket(p, raw):
    n = _norm(raw)
    score = 0
    if p["class"] == "INVENTORY_GROUP": score += 5
    if p["class"] == "REQUIREMENT": score += 4
    if p["class"] in {"NOISE","FRAGMENT","UNRESOLVED"}: score += 4
    if p["transaction"] in {"AMBIGUOUS","UNKNOWN"}: score += 4
    if "pre" in n and ("lease" in n or "rent" in n): score += 3
    if len(raw or "") > 800: score += 3
    if any(x in n for x in ["looking for","client wants","getting vacated","available for lease","inventory"]): score += 2
    return score

def _select_v5(pool):
    if len(pool) < V5_TARGET:
        raise RuntimeError(f"Only {len(pool)} untouched blind cases remain; need {V5_TARGET}.")
    enriched = []
    for r in pool:
        p = predict_message(r["raw_text"])
        tie = int(hashlib.sha256((V5_EXAM_VERSION + str(r["blind_id"])).encode()).hexdigest()[:12],16)
        enriched.append((r,p,_risk_bucket(p,r["raw_text"]),tie))

    groups = {}
    for item in enriched:
        key = (item[1]["class"], item[1]["transaction"])
        groups.setdefault(key,[]).append(item)
    for vals in groups.values():
        vals.sort(key=lambda x:(-x[2],x[3]))

    selected = []
    for key in sorted(groups):
        if groups[key] and len(selected) < V5_TARGET:
            selected.append(groups[key].pop(0))
    remaining = [x for vals in groups.values() for x in vals]
    remaining.sort(key=lambda x:(-x[2],x[3]))
    for x in remaining:
        if len(selected) >= V5_TARGET: break
        selected.append(x)
    selected.sort(key=lambda x:int(hashlib.sha256(("V5ORDER|"+str(x[0]["blind_id"])).encode()).hexdigest()[:12],16))
    return selected[:V5_TARGET]

def _predictor_hash():
    try:
        return hashlib.sha256(inspect.getsource(predict_message).encode("utf-8")).hexdigest()
    except Exception:
        return "UNAVAILABLE"

def freeze_v5(engine):
    _install(engine)
    train = training_status(engine)
    if train["training_gate"] != "V430_TRAINING_PASS_READY_FOR_FRESH_V5":
        return {"status":"BLOCKED","reason":"Student 4.3 training/regression gate is HOLD.","training":train}

    with engine.connect() as conn:
        existing = conn.execute(text(
            "SELECT COUNT(*) FROM alliance_v5_cases WHERE exam_version=:e"
        ), {"e":V5_EXAM_VERSION}).scalar() or 0
        if existing:
            manifest = conn.execute(text(
                "SELECT predictor_sha256,case_manifest_hash,status FROM alliance_v5_manifest WHERE exam_version=:e"
            ), {"e":V5_EXAM_VERSION}).mappings().first()
            return {"status":"ALREADY_FROZEN","total":int(existing),"manifest":dict(manifest) if manifest else None}

    selected = _select_v5(_candidate_pool(engine))
    psha = _predictor_hash()
    payload = [{
        "blind_id":str(r["blind_id"]),
        "source_hash":r["source_hash"],
        "predicted_class":p["class"],
        "predicted_transaction":p["transaction"],
        "predicted_ownership":p["ownership"],
    } for r,p,_,_ in selected]
    mhash = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()

    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_v5_manifest
        (manifest_id,exam_version,target,predictor_version,predictor_sha256,selection_policy,case_manifest_hash,status)
        VALUES(:id,:e,:t,:pv,:ps,:policy,:mh,'FROZEN')"""),
        {"id":str(uuid.uuid4()),"e":V5_EXAM_VERSION,"t":V5_TARGET,"pv":VERSION,"ps":psha,
         "policy":"Untouched 3.3 blind pool only; excludes V1/V2/V3/V4/V5 IDs; diversity-first selection uses frozen Student 4.3 predictions only; no V5 truth used.",
         "mh":mhash})
        for ordinal,(r,p,_,_) in enumerate(selected,1):
            conn.execute(text("""INSERT INTO alliance_v5_cases
            (audit_id,blind_id,exam_version,ordinal,source_hash,raw_text,predicted_class,predicted_transaction,
             predicted_ownership,prediction_confidence,prediction_rule)
            VALUES(:id,:bid,:e,:ord,:sh,:raw,:cl,:tx,:ow,:cf,:rule)"""),
            {"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"e":V5_EXAM_VERSION,"ord":ordinal,
             "sh":r["source_hash"],"raw":r["raw_text"],"cl":p["class"],"tx":p["transaction"],
             "ow":p["ownership"],"cf":float(p["confidence"]),"rule":p.get("rule")})
    return {"status":"FROZEN","total":V5_TARGET,"predictor_sha256":psha,"case_manifest_hash":mhash}

# ---------------------------------------------------------------------------
# Independent V5 examiner. It never calls Student 4.3.
# Frozen truth stack = 4.2.1 judges + 4.2.2 semantic + 4.2.3 grammar +
# 4.2.5 acquisition-intent. This stack existed before V5 was selected.
# ---------------------------------------------------------------------------

def _exam_judges(engine, raw):
    out = {}
    for name,j in v421._judges(engine,raw).items():
        out[name] = {
            "class":j[0],"transaction":j[1],"ownership":j[2],
            "class_confidence":float(j[3]),"transaction_confidence":float(j[4]),
            "ownership_confidence":float(j[5]),"evidence":j[6],
        }

    for name,fn in [
        ("G_V422_SEMANTIC", v422.semantic_truth),
        ("H_V423_DUAL_GRAMMAR", v423.rescue_truth),
        ("I_V425_DUAL_ACQUISITION", v425.acquisition_truth),
    ]:
        j = fn(raw)
        out[name] = {
            "class":j[0],"transaction":j[1],"ownership":j[2],
            "class_confidence":float(j[3]),"transaction_confidence":float(j[3]),
            "ownership_confidence":float(j[3]),"evidence":j[4],
        }
    return out

def _resolve_field(judges, field):
    conf_key = f"{field}_confidence"
    votes = []
    for name,j in judges.items():
        val = j.get(field)
        cf = float(j.get(conf_key) or 0)
        if val and cf >= 0.95:
            votes.append((name,val,cf))
    if not votes:
        return {"status":"UNRESOLVED","reason":"NO_QUALIFIED_VOTES"}

    by = defaultdict(list)
    for name,val,cf in votes:
        by[val].append((name,cf))
    winner, wvotes = max(by.items(), key=lambda kv:(len(kv[1]), sum(x[1] for x in kv[1])))
    winner_count = len(wvotes)
    avg = sum(x[1] for x in wvotes)/winner_count
    strong_dissent = [(name,val,cf) for name,val,cf in votes if val != winner and cf >= 0.985]

    semantic_core = {"A_EVIDENCE_CONTRACT","B_COUNTERFACTUAL_CRITIC","D_CRE_DECISION_GRAPH","F_INTENT_HIERARCHY"}
    core_winners = sum(1 for name,cf in wvotes if name in semantic_core)

    accepted = (
        (winner_count >= 4 and avg >= 0.96 and len(strong_dissent) <= 1)
        or (winner_count >= 3 and avg >= 0.975 and core_winners >= 2 and not strong_dissent)
    )
    if not accepted:
        return {
            "status":"UNRESOLVED","majority":winner,"count":winner_count,
            "avg_confidence":round(avg,4),"strong_dissent":strong_dissent,
            "votes":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes],
        }
    return {
        "status":"RESOLVED","value":winner,"confidence":round(avg,4),
        "votes":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes],
        "dissent":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes if v != winner],
    }

def _adjudicate(engine):
    freeze = freeze_v5(engine)
    if freeze.get("status") == "BLOCKED":
        return {"status":"BLOCKED","freeze":freeze}

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(
            "SELECT * FROM alliance_v5_cases WHERE exam_version=:e ORDER BY ordinal"
        ), {"e":V5_EXAM_VERSION}).mappings()]

    for r in rows:
        with engine.connect() as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM alliance_v5_truth WHERE audit_id=:id"
            ), {"id":str(r["audit_id"])}).scalar()
        if exists:
            continue

        judges = _exam_judges(engine,r["raw_text"])
        fields = {f:_resolve_field(judges,f) for f in ("class","transaction","ownership")}
        ok = all(v.get("status") == "RESOLVED" for v in fields.values())
        cls = fields["class"].get("value") if ok else None
        tx = fields["transaction"].get("value") if ok else None
        ow = fields["ownership"].get("value") if ok else None
        cc = fields["class"].get("confidence",0)
        tc = fields["transaction"].get("confidence",0)
        oc = fields["ownership"].get("confidence",0)
        consensus = {
            "status":"AUTO_RESOLVED" if ok else "EXCEPTION",
            "fields":fields,
            "judge_names":list(judges.keys()),
            "policy":"Frozen pre-V5 independent truth stack. Student 4.3 is not a judge.",
        }
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_v5_truth
            (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,
             class_confidence,transaction_confidence,ownership_confidence,consensus,status)
            VALUES(:id,:aid,:e,:cl,:tx,:ow,:cc,:tc,:oc,CAST(:con AS JSONB),:st)"""),
            {"id":str(uuid.uuid4()),"aid":str(r["audit_id"]),"e":V5_EXAM_VERSION,
             "cl":cls,"tx":tx,"ow":ow,"cc":cc,"tc":tc,"oc":oc,
             "con":_j(consensus),"st":"AUTO_RESOLVED" if ok else "EXCEPTION"})
    return v5_report(engine)

def v5_report(engine):
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
        SELECT c.*,t.truth_class,t.truth_transaction,t.truth_ownership,t.status truth_status,t.consensus
        FROM alliance_v5_cases c
        LEFT JOIN alliance_v5_truth t ON t.audit_id=c.audit_id
        WHERE c.exam_version=:e ORDER BY c.ordinal
        """), {"e":V5_EXAM_VERSION}).mappings()]

    unresolved = [{
        "ordinal":r["ordinal"],"audit_id":str(r["audit_id"]),
        "reason":(r["consensus"] or {}).get("fields") if isinstance(r["consensus"],dict) else None
    } for r in rows if r["truth_status"] != "AUTO_RESOLVED"]

    if unresolved:
        return {
            "version":VERSION,"exam_version":V5_EXAM_VERSION,"total":len(rows),
            "auto_resolved":len(rows)-len(unresolved),"unresolved":len(unresolved),
            "unresolved_cases":unresolved,
            "certification_gate":"V5_AUTOMATED_TRUTH_INCOMPLETE_EXCEPTION_ONLY",
            "manual_work_required":0,
            "safety":{"student_tuning_during_v5":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0},
        }

    fs = {f:[0,0] for f in ("class","transaction","ownership")}
    errors = []
    case_ok = 0
    truth_payload = []
    for r in rows:
        t = {"class":r["truth_class"],"transaction":r["truth_transaction"],"ownership":r["truth_ownership"]}
        p = {"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        truth_payload.append((str(r["audit_id"]),t["class"],t["transaction"],t["ownership"]))
        ok = True
        for f in fs:
            fs[f][1] += 1
            if p[f] == t[f]:
                fs[f][0] += 1
            else:
                ok = False
                errors.append({"ordinal":r["ordinal"],"field":f,"truth":t[f],"student":p[f]})
        case_ok += int(ok)

    cmp = sum(v[1] for v in fs.values())
    cor = sum(v[0] for v in fs.values())
    acc = round(100*cor/cmp,4)
    fa = {k:round(100*v[0]/v[1],4) for k,v in fs.items()}
    ca = round(100*case_ok/len(rows),4)
    gate = "AUTOMATED_INDEPENDENT_V5_PASS" if (
        acc >= OVERALL_PASS and all(v >= FIELD_PASS for v in fa.values())
    ) else "AUTOMATED_INDEPENDENT_V5_HOLD"

    result = {
        "version":VERSION,"exam_version":V5_EXAM_VERSION,"total":len(rows),
        "auto_resolved":len(rows),"unresolved":0,"manual_work_required":0,
        "correct_fields":cor,"comparable_fields":cmp,"accuracy":acc,
        "field_accuracy":fa,"case_accuracy":ca,"errors":errors,
        "certification_gate":gate,
        "truth_policy":"Frozen 4.2.1/4.2.2/4.2.3/4.2.5 examiner stack; Student 4.3 excluded from truth generation.",
        "safety":{"student_tuning_during_v5":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0},
    }
    th = hashlib.sha256(json.dumps(truth_payload,separators=(",",":")).encode()).hexdigest()

    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_v5_results
        (result_id,exam_version,total_cases,auto_resolved,unresolved,comparable_fields,correct_fields,
         overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,
         certification_gate,truth_hash,result)
        VALUES(:id,:e,:tot,:a,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
        ON CONFLICT(exam_version) DO NOTHING"""),
        {"id":str(uuid.uuid4()),"e":V5_EXAM_VERSION,"tot":len(rows),"a":len(rows),
         "cmp":cmp,"cor":cor,"oa":acc,"ca":fa["class"],"ta":fa["transaction"],
         "ow":fa["ownership"],"casea":ca,"gate":gate,"th":th,"res":_j(result)})

    with engine.connect() as conn:
        stored = conn.execute(text(
            "SELECT result FROM alliance_v5_results WHERE exam_version=:e"
        ), {"e":V5_EXAM_VERSION}).scalar()
    return stored or result

def run(engine):
    _install(engine)
    train = training_status(engine)
    _record_training(engine,train)

    if train["training_gate"] != "V430_TRAINING_PASS_READY_FOR_FRESH_V5":
        return {
            "version":VERSION,"status":"TRAINING_HOLD","training":train,
            "v5":{"status":"NOT_FROZEN"},
            "next_step":"Automation must repair training/regression before V5 can be frozen.",
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0},
        }

    frozen = freeze_v5(engine)
    exam = _adjudicate(engine)
    return {
        "version":VERSION,"status":"V5_RUNNING_OR_COMPLETE",
        "training":train,"v5_freeze":frozen,"v5_exam":exam,
        "next_step":"If V5 truth has exceptions, automate only those exceptions. If complete, accept PASS/HOLD without tuning V5.",
        "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0},
    }

def _dashboard(engine):
    s = run(engine)
    tr = s.get("training",{})
    ex = s.get("v5_exam",{})
    if s.get("status") == "TRAINING_HOLD":
        banner = "<div class='warn'>Training/regression gate HOLD. V5 was NOT frozen.</div>"
    elif ex.get("certification_gate") == "AUTOMATED_INDEPENDENT_V5_PASS":
        banner = "<div class='ok'>✓ STUDENT 4.3 PASSED FRESH AUTOMATED V5 CERTIFICATION</div>"
    elif ex.get("certification_gate") == "AUTOMATED_INDEPENDENT_V5_HOLD":
        banner = "<div class='warn'>V5 complete: Student 4.3 HOLD. V5 remains immutable.</div>"
    else:
        banner = f"<div class='warn'>V5 automated truth exceptions: {ex.get('unresolved','—')}. No manual work requested.</div>"

    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Autonomous Student 4.3</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1200px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
    .card{{background:#fff;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}
    strong{{display:block;font-size:25px;margin-top:8px}}.ok{{background:#e8f8ee;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}
    .warn{{background:#fff4cf;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}
    pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px}}</style></head><body>
    <h1>Alliance Autonomous Student 4.3 → Fresh V5</h1>
    <p>V4 is closed training data. Student 4.3 is regression-gated before a fresh unseen V5 is frozen. V5 truth uses only the frozen pre-V5 examiner stack.</p>
    <div class='grid'>
      <div class='card'>V4 Training<strong>{tr.get('v4_training',{}).get('accuracy','—')}%</strong></div>
      <div class='card'>Legacy Regression<strong>{tr.get('legacy_regression',{}).get('accuracy','—')}%</strong></div>
      <div class='card'>Lesson Regression<strong>{tr.get('lesson_regression',{}).get('accuracy','—')}%</strong></div>
      <div class='card'>V5 Auto Resolved<strong>{ex.get('auto_resolved','—')}</strong></div>
      <div class='card'>V5 Remaining<strong>{ex.get('unresolved','—')}</strong></div>
      <div class='card'>V5 Accuracy<strong>{ex.get('accuracy','—')}</strong></div>
      <div class='card'>V5 Gate<strong style='font-size:14px'>{html.escape(str(ex.get('certification_gate','NOT READY')))}</strong></div>
    </div>{banner}
    <h2>Machine Report</h2><pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre>
    </body></html>"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app,"/api/property-brain/autonomous-v430/status"):
        @app.get("/api/property-brain/autonomous-v430/status")
        def status_v430():
            return run(engine)

    if not foundation._route_exists(app,"/property-brain/autonomous-v430"):
        @app.get("/property-brain/autonomous-v430",response_class=HTMLResponse)
        def page_v430():
            return HTMLResponse(_dashboard(engine))

    try:
        run(engine)
    except Exception:
        pass

    return {
        "status":"REGISTERED","version":VERSION,"route":"/property-brain/autonomous-v430",
        "policy":"V4_CLOSED_TRAINING_FRESH_V5_AUTOMATED_CERTIFICATION",
        "production_writes":0,"whatsapp_writes":0,"gold_mutations":0,
    }
