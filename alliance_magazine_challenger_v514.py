from __future__ import annotations

import hashlib
import inspect
import re

import alliance_magazine_challenger_v513 as base
import alliance_magazine_challenger_v512 as v512
import alliance_magazine_academy_v510 as legacy
import alliance_autonomous_student_v438 as champion

VERSION = "5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
MODE = "SEPARATE_CHALLENGER_V3_FAILURE_LESSON_PLUS_V2_V1_LEGACY_REGRESSION_NO_SOURCE_MUTATION"
BASE_VERSION = "5.1.3-ALLIANCE-MAGAZINE-CHALLENGER-V2-FAILURE-CLOSURE"
V512_VERSION = "5.1.2-ALLIANCE-MAGAZINE-CHALLENGER-V1-FAILURE-CLOSURE"
LEGACY_VERSION = "5.1.1-ALLIANCE-MAGAZINE-AUTONOMOUS-ACADEMY-SEMANTIC-CLOSURE"
CHAMPION_VERSION = "4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
CHAMPION_SHA256 = "8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _asset_class(raw, category="", predicted_class=""):
    n = _norm(raw)
    cat = _norm(category)
    cls = str(predicted_class or "")

    # When the record is a REQUIREMENT, distinguish the user's business/tenant
    # profile from the physical CRE asset being sought.
    # Examples:
    # "restaurant chain looking for high-street premises" -> COMMERCIAL,
    # not HOSPITALITY merely because the occupier is a restaurant operator.
    if cls == "REQUIREMENT":
        if re.search(r"\b(?:warehouse|factory|industrial\s+(?:facility|plot|shed)|godown|manufacturing\s+unit)\b", n) or re.search(r"\bindustrial\b", cat):
            return "INDUSTRIAL"
        if re.search(r"\b(?:hotel|resort|guest\s*house|banquet\s+property)\b", n) and re.search(
            r"\b(?:buy|purchase|acquire|lease|rent|looking\s+for|seeking|requires?)\b", n
        ):
            return "HOSPITALITY"
        if re.search(r"\b(?:land\s+parcel|vacant\s+land|plot|farm\s*land|farmland|acre\s+land)\b", n) or re.search(r"\b(?:land|plot)\b", cat):
            return "LAND_OR_PLOT"
        if re.search(r"\b(?:flat|apartment|villa|builder\s*floor|independent\s*floor|bungalow|house|bhk)\b", n) or re.search(r"\bresidential\b", cat):
            return "RESIDENTIAL"
        # Offices, shops, showrooms, stores, high-street premises and generic
        # commercial premises remain COMMERCIAL irrespective of the occupier
        # being a restaurant, salon, gym, clinic, bank, fashion brand, etc.
        if re.search(r"\b(?:office|shop|showroom|store|retail|commercial|premises|space|high[- ]street|sco)\b", n) or re.search(
            r"\b(?:office|retail|commercial)\b", cat
        ):
            return "COMMERCIAL"

    return base._asset_class(raw, category)

def analyze(row):
    a = base.analyze(dict(row))
    raw = str(legacy._row_get(row, "original_raw_text", "raw_text", "remarks", "description", "configuration") or "")
    category = str(legacy._row_get(row, "category", "property_type") or "")
    a["predicted_asset_class"] = _asset_class(raw, category, a.get("predicted_class"))
    return a

V3_FAILURE_CURRICULUM = [
    ("operator_profile_not_property_asset",
     {"source_id":"T1","listing_type":"Wanted","category":"Retail","locality":"Delhi NCR","area":"6000","area_unit":"SQFT","price":"","valid_mobiles":"9811110019","original_raw_text":"Restaurant chain looking for 4500-6000 sqft high-street premises on rent across Delhi NCR."},
     {"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN"}),
]

def _projection(a):
    return {
        "class":a.get("predicted_class"),
        "transaction":a.get("predicted_transaction"),
        "asset":a.get("predicted_asset_class"),
        "occupancy":a.get("occupancy_status"),
        "atomicity":a.get("atomicity_status"),
        "price_kind":a.get("price_kind"),
        "locality":(a.get("evidence") or {}).get("locality_status"),
        "reasons":a.get("risk_reasons") or [],
    }

def score_training():
    errors=[]; total=0; correct=0

    # V3 failure lesson.
    for name,row,truth in V3_FAILURE_CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in truth.items():
            total += 1
            if p.get(field)==exp: correct += 1
            else: errors.append({"suite":"V3_FAILURE_LESSON","name":name,"field":field,"expected":exp,"got":p.get(field)})

    # V2 failure regressions from 5.1.3.
    for name,row,truth in base.V2_FAILURE_CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in truth.items():
            total += 1
            if p.get(field)==exp: correct += 1
            else: errors.append({"suite":"V2_FAILURE_REGRESSION","name":name,"field":field,"expected":exp,"got":p.get(field)})

    # V1 failure regressions from 5.1.2.
    for name,row,truth in v512.FAILURE_CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in truth.items():
            total += 1
            if p.get(field)==exp: correct += 1
            else: errors.append({"suite":"V1_FAILURE_REGRESSION","name":name,"field":field,"expected":exp,"got":p.get(field)})

    # Original 5.1.1 curriculum.
    for name,row,exp_cls,exp_tx,exp_reason in legacy.CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in (("class",exp_cls),("transaction",exp_tx)):
            total += 1
            if p.get(field)==exp: correct += 1
            else: errors.append({"suite":"LEGACY_511","name":name,"field":field,"expected":exp,"got":p.get(field)})
        total += 1
        if exp_reason in p["reasons"]: correct += 1
        else: errors.append({"suite":"LEGACY_511","name":name,"field":"lesson","expected":exp_reason,"got":p["reasons"]})

    return {
        "v3_failure_cases":len(V3_FAILURE_CURRICULUM),
        "v2_failure_regression_cases":len(base.V2_FAILURE_CURRICULUM),
        "v1_failure_regression_cases":len(v512.FAILURE_CURRICULUM),
        "legacy_cases":len(legacy.CURRICULUM),
        "checks":total,
        "correct":correct,
        "accuracy":round(100*correct/max(total,1),4),
        "errors":errors,
        "gate":"CHALLENGER_READY_FOR_FRESH_V4" if correct==total else "CHALLENGER_HOLD",
    }

def champion_hash():
    try:
        src=inspect.getsource(champion.predict_message)+inspect.getsource(champion.leading_demand_object)
        return hashlib.sha256(src.encode()).hexdigest()
    except Exception:
        return "UNAVAILABLE"

def self_check():
    if base.VERSION != BASE_VERSION:
        return {"status":"ERROR","error":f"Parent Challenger changed: {base.VERSION}"}
    if v512.VERSION != V512_VERSION:
        return {"status":"ERROR","error":f"V1 Challenger changed: {v512.VERSION}"}
    if legacy.VERSION != LEGACY_VERSION:
        return {"status":"ERROR","error":f"Legacy student changed: {legacy.VERSION}"}
    if champion.VERSION != CHAMPION_VERSION:
        return {"status":"ERROR","error":f"Champion changed: {champion.VERSION}"}
    ch=champion_hash()
    if ch != CHAMPION_SHA256:
        return {"status":"ERROR","error":f"Champion hash changed: {ch}"}
    score=score_training()
    return {
        "version":VERSION,
        "mode":MODE,
        "status":"TRAINING_PASS" if score["gate"]=="CHALLENGER_READY_FOR_FRESH_V4" else "TRAINING_HOLD",
        "parent_challenger":{"version":base.VERSION,"immutable":True},
        "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
        "training":score,
        "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,
                  "champion_mutations":0,"parent_challenger_mutations":0},
    }
