from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

import alliance_magazine_academy_v510 as base
import alliance_autonomous_student_v438 as champion

VERSION = "5.1.2-ALLIANCE-MAGAZINE-CHALLENGER-V1-FAILURE-CLOSURE"
MODE = "SEPARATE_CHALLENGER_EXACT_V1_FAILURE_LESSONS_PLUS_LEGACY_REGRESSION_NO_SOURCE_MUTATION"
BASE_VERSION = "5.1.1-ALLIANCE-MAGAZINE-AUTONOMOUS-ACADEMY-SEMANTIC-CLOSURE"
CHAMPION_VERSION = "4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
CHAMPION_SHA256 = "8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _transaction(raw, listing_type=""):
    lt = _norm(listing_type)
    n = _norm((listing_type or "") + " " + (raw or ""))

    if re.search(r"\b(?:sale\s*(?:/|&|or|and)\s*(?:rent|lease)|(?:rent|lease)\s*(?:/|&|or|and)\s*sale)\b", n):
        return "AMBIGUOUS"

    occupied = bool(re.search(
        r"\b(?:pre[- ]?rented|pre[- ]?leased|pre[- ]?tenanted|leased\s+to|rented\s+to|"
        r"rented\s+(?:shop|office|showroom|property|building))\b", n
    ))
    sale = bool(re.search(r"\b(?:sale|resale|buy|purchase)\b", lt)) or bool(re.search(
        r"\b(?:for\s+sale|outright\s+sale|sale\b|resale|asking\s+(?:rs\.?|₹|\d)|price\s+(?:rs\.?|₹|\d))\b", n
    ))
    rent = bool(re.search(r"\b(?:rent|rental|lease)\b", lt)) or bool(re.search(
        r"\b(?:for\s+rent|on\s+rent|to[- ]?let|rental\b|for\s+lease|on\s+lease|available\s+for\s+lease|"
        r"company\s+lease|lease\s+out|wants?\s+to\s+rent|wants?\s+to\s+lease|required.*?\bon\s+rent\b)\b", n
    ))

    if sale and occupied:
        return "SALE"
    if sale and rent:
        return "AMBIGUOUS"
    if sale:
        return "SALE"
    if rent:
        return "RENT"
    return "UNKNOWN"

def _asset_class(raw, category=""):
    n = _norm(raw)
    cat = _norm(category)

    # Strong semantic object wins over generic area/reference words.
    if re.search(r"\b(?:builder\s*floor|independent\s*floor|flat|apartment|villa|kothi|house|4\s*bhk|3\s*bhk|2\s*bhk|5\s*bhk|bhk)\b", n):
        return "RESIDENTIAL"
    if re.search(r"\b(?:industrial|factory|shed|warehouse|godown|manufacturing\s+unit)\b", n) or re.search(r"\bindustrial\b", cat):
        return "INDUSTRIAL"
    if re.search(r"\b(?:hotel|banquet|guest\s*house|resort|restaurant|cafe|club|lounge)\b", n) or re.search(r"\b(?:hotel|hospitality)\b", cat):
        return "HOSPITALITY"
    if re.search(r"\b(?:office|showroom|shop|retail|commercial|sco|business\s+centre)\b", n) or re.search(r"\b(?:office|retail|commercial)\b", cat):
        return "COMMERCIAL"
    if re.search(r"\b(?:vacant\s+plot|bungalow\s+plot|farm\s*land|farmland|agricultural|land\s+parcel|plot\s+for\s+sale|industrial\s+plot)\b", n):
        return "LAND_OR_PLOT"
    if re.search(r"\b(?:residential|flat|apartment|floor|villa|house|bungalow)\b", cat):
        return "RESIDENTIAL"
    if re.search(r"\b(?:land|plot)\b", cat):
        return "LAND_OR_PLOT"
    return base._asset_class(raw, category)

def _price_kind(raw, price):
    p = _norm(price)
    if not p:
        return "UNKNOWN"

    # Budget is demand-side financial constraint, not the property's price.
    if re.match(r"^(?:budget|max(?:imum)?\s+budget|upto\s+budget|up\s+to\s+budget)\b", p):
        return "TEXT_PRICE"

    # Area-rate is a rate. Time period alone (₹22 lakh/month) is a rent amount,
    # not a per-area rate.
    if re.search(r"(?:/|per)\s*(?:sq\.?\s*ft|sqft|sft|sq\.?\s*yd|sqyd|sqm|sq\.?\s*m)\b", p):
        return "RATE_OR_RENT_RATE"

    if re.search(r"\b(?:cr|crore|crores|lac|lakh|lakhs|₹|rs\.?|inr)\b", p):
        return "MONEY_AMOUNT"
    if re.fullmatch(r"[\d,.]+", p):
        return "BARE_NUMBER"
    return "TEXT_PRICE"

def analyze(row):
    a = base.analyze(dict(row))
    raw = str(base._row_get(row, "original_raw_text", "raw_text", "remarks", "description", "configuration") or "")
    listing = str(base._row_get(row, "listing_type", "lead_type", "transaction", "record_status") or "")
    category = str(base._row_get(row, "category", "property_type") or "")
    price = base._row_get(row, "price", "asking_price")

    a["predicted_transaction"] = _transaction(raw, listing)
    a["predicted_asset_class"] = _asset_class(raw, category)
    a["price_kind"] = _price_kind(raw, price)

    # Preserve all base safety/risk reasoning. Only add evidence-based lessons.
    reasons = set(a.get("risk_reasons") or [])
    if a["price_kind"] == "BARE_NUMBER" and not base._bare_price_supported_by_same_number(raw, price):
        reasons.add("NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE")
    a["risk_reasons"] = sorted(reasons)
    if a["risk_reasons"]:
        a["auto_status"] = "REVIEW"
    return a

FAILURE_CURRICULUM = [
    ("monthly_rent_amount",
     {"source_id":"R1","listing_type":"Rent","category":"Hospitality","locality":"Aerocity","area":"18000","area_unit":"SQFT","price":"₹22 Lac/month","valid_mobiles":"9811919191","original_raw_text":"Restaurant 18,000 sq ft available on lease, rent ₹22 lakh/month."},
     {"transaction":"RENT","asset":"HOSPITALITY","price_kind":"MONEY_AMOUNT"}),
    ("requirement_budget_not_price",
     {"source_id":"R2","listing_type":"Requirement - Buy","category":"Industrial","locality":"Faridabad","area":"2","area_unit":"ACRE","price":"Budget ₹15 Cr","valid_mobiles":"9999912345","original_raw_text":"Client wants to purchase 1.5 to 2 acre industrial plot in Faridabad. Budget up to ₹15 Cr."},
     {"transaction":"SALE","asset":"INDUSTRIAL","price_kind":"TEXT_PRICE"}),
    ("builder_floor_not_plot",
     {"source_id":"R3","listing_type":"Sale","category":"Residential","locality":"Greater Kailash 1","area":"300","area_unit":"SQYD","price":"₹9.25 Cr","valid_mobiles":"9810099999","original_raw_text":"GK-1 builder floor, plot 300 sq yds, 4 BHK, second floor. For sale ₹9.25 Cr."},
     {"transaction":"SALE","asset":"RESIDENTIAL","price_kind":"MONEY_AMOUNT"}),
    ("residential_group_category",
     {"source_id":"R4","listing_type":"Sale","category":"Residential","locality":"South Delhi","area":"","area_unit":"","price":"Varies","valid_mobiles":"9810011111","original_raw_text":"Available options: Defence Colony 325 yds ₹14 Cr; Panchsheel Park 500 yds ₹22 Cr; Vasant Vihar 600 yds ₹28 Cr."},
     {"transaction":"SALE","asset":"RESIDENTIAL","price_kind":"TEXT_PRICE"}),
    ("requirement_on_rent",
     {"source_id":"R5","listing_type":"Wanted","category":"Warehouse","locality":"NH-8","area":"50000","area_unit":"SQFT","price":"","valid_mobiles":"9810707070","original_raw_text":"Required warehouse 40,000-50,000 sqft on rent near NH-8 / Bilaspur for logistics company."},
     {"transaction":"RENT","asset":"INDUSTRIAL","price_kind":"UNKNOWN"}),
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
        "contact_quality":a.get("contact_quality"),
        "reasons":a.get("risk_reasons") or [],
    }

def score_training():
    errors=[]
    total=0
    correct=0

    # Exact frozen V1 failures, now legal training material after exam completion.
    for name,row,truth in FAILURE_CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in truth.items():
            total+=1
            if p.get(field)==exp:
                correct+=1
            else:
                errors.append({"suite":"V1_FAILURE_LESSONS","name":name,"field":field,"expected":exp,"got":p.get(field)})

    # Legacy 5.1.1 curriculum must remain 100%.
    for name,row,exp_cls,exp_tx,exp_reason in base.CURRICULUM:
        p=_projection(analyze(row))
        checks=[("class",exp_cls),("transaction",exp_tx)]
        for field,exp in checks:
            total+=1
            if p.get(field)==exp:
                correct+=1
            else:
                errors.append({"suite":"LEGACY_511","name":name,"field":field,"expected":exp,"got":p.get(field)})
        total+=1
        if exp_reason in p["reasons"]:
            correct+=1
        else:
            errors.append({"suite":"LEGACY_511","name":name,"field":"lesson","expected":exp_reason,"got":p["reasons"]})

    return {
        "failure_cases":len(FAILURE_CURRICULUM),
        "legacy_cases":len(base.CURRICULUM),
        "checks":total,
        "correct":correct,
        "accuracy":round(100*correct/max(total,1),4),
        "errors":errors,
        "gate":"CHALLENGER_READY_FOR_FRESH_V2" if correct==total else "CHALLENGER_HOLD",
    }

def champion_hash():
    try:
        src=inspect.getsource(champion.predict_message)+inspect.getsource(champion.leading_demand_object)
        return hashlib.sha256(src.encode()).hexdigest()
    except Exception:
        return "UNAVAILABLE"

def self_check():
    if base.VERSION != BASE_VERSION:
        return {"status":"ERROR","error":f"Base version changed: {base.VERSION}"}
    if champion.VERSION != CHAMPION_VERSION:
        return {"status":"ERROR","error":f"Champion version changed: {champion.VERSION}"}
    ch=champion_hash()
    if ch != CHAMPION_SHA256:
        return {"status":"ERROR","error":f"Champion hash changed: {ch}"}
    score=score_training()
    return {
        "version":VERSION,
        "mode":MODE,
        "status":"TRAINING_PASS" if score["gate"]=="CHALLENGER_READY_FOR_FRESH_V2" else "TRAINING_HOLD",
        "base_student":{"version":base.VERSION,"immutable":True},
        "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
        "training":score,
        "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,"champion_mutations":0,"base_student_mutations":0},
    }
