from __future__ import annotations

import hashlib
import inspect
import re

import alliance_magazine_challenger_v512 as base
import alliance_magazine_academy_v510 as legacy
import alliance_autonomous_student_v438 as champion

VERSION = "5.1.3-ALLIANCE-MAGAZINE-CHALLENGER-V2-FAILURE-CLOSURE"
MODE = "SEPARATE_CHALLENGER_V2_FAILURE_LESSONS_PLUS_V1_AND_LEGACY_REGRESSION_NO_SOURCE_MUTATION"
BASE_VERSION = "5.1.2-ALLIANCE-MAGAZINE-CHALLENGER-V1-FAILURE-CLOSURE"
LEGACY_VERSION = "5.1.1-ALLIANCE-MAGAZINE-AUTONOMOUS-ACADEMY-SEMANTIC-CLOSURE"
CHAMPION_VERSION = "4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
CHAMPION_SHA256 = "8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _classify(raw, listing_type=""):
    n = _norm((listing_type or "") + " " + (raw or ""))
    lt = _norm(listing_type)

    demand = bool(re.search(
        r"\b(?:requirement|required|requires?|wanted|looking\s+for|looking\s+to|seeking|"
        r"need(?:ed|s)?|wants?\s+to)\b", n
    ))
    demand_asset = bool(re.search(
        r"\b(?:property|properties|asset|assets|premises|facility|facilities|plot|land|"
        r"flat|apartment|office|shop|showroom|building|warehouse|factory|industrial|"
        r"farmhouse|hotel|floor|space|villa|kothi|restaurant|retail|rooms?|bhk)\b", n
    ))
    if demand and demand_asset:
        return "REQUIREMENT"

    if re.search(r"\b(?:sale|resale|rent|rental|lease|available)\b", lt):
        return "PROPERTY_AVAILABILITY"

    return base.analyze({
        "original_raw_text": raw,
        "listing_type": listing_type,
        "category": "",
        "locality": "",
        "area": "",
        "area_unit": "",
        "price": "",
        "valid_mobiles": ""
    }).get("predicted_class") or "UNKNOWN"

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
        r"\b(?:for\s+sale|outright\s+sale|sale\b|resale|"
        r"(?:wants?|looking|seeking|required|requirement|need(?:s|ed)?)\s+(?:to\s+)?(?:buy|purchase|acquire)|"
        r"looking\s+to\s+(?:buy|purchase|acquire)|"
        r"asking\s+(?:rs\.?|₹|\d)|price\s+(?:rs\.?|₹|\d))\b", n
    ))

    rent = bool(re.search(r"\b(?:rent|rental|lease)\b", lt)) or bool(re.search(
        r"\b(?:for\s+rent|on\s+rent|to[- ]?let|rental\b|for\s+lease|on\s+lease|"
        r"available\s+for\s+lease|company\s+lease|lease\s+out|"
        r"(?:wants?|looking|seeking|required|requires?|requirement|need(?:s|ed)?)\s+(?:to\s+)?(?:rent|lease)|"
        r"looking\s+to\s+(?:rent|lease))\b", n
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

def _occupancy(raw, asset_class):
    n = _norm(raw)
    if re.search(r"\b(?:pre[- ]?rented|pre[- ]?leased|pre[- ]?tenanted|leased\s+to|rented\s+to)\b", n):
        return "TENANTED"

    # "Vacant land/plot" describes development state, not premises occupancy.
    if asset_class == "LAND_OR_PLOT" and re.search(
        r"\b(?:vacant\s+(?:land|plot|parcel)|vacant\s+farmhouse\s+land|land\s+parcel)\b", n
    ):
        return "UNKNOWN"

    if re.search(r"\b(?:vacant|ready\s+possession|ready\s+to\s+move|vacant\s+possession)\b", n):
        return "VACANT_OR_READY"
    return "UNKNOWN"

def _asset_class(raw, category=""):
    n = _norm(raw)
    cat = _norm(category)

    # Explicit plot/land head wins when the marketed asset is the land itself.
    if re.search(
        r"\b(?:bungalow\s+plot|vacant\s+(?:land|plot)|land\s+parcel|farmhouse\s+land|"
        r"redevelopment\s+(?:plot|site)|plot\s+\d[\d,.]*\s*(?:sq\s*yd|sqyd|yards?|acres?)|"
        r"plot\s+for\s+sale)\b", n
    ):
        return "LAND_OR_PLOT"

    # A floor/apartment/villa remains residential even when its parent plot size is stated.
    if re.search(
        r"\b(?:builder\s*floor|independent\s*floor|flat|apartment|villa|kothi|"
        r"\d+\s*bhk|bhk)\b", n
    ):
        return "RESIDENTIAL"

    if re.search(r"\b(?:industrial|factory|shed|warehouse|godown|manufacturing\s+unit|industrial\s+facility)\b", n) or re.search(r"\bindustrial\b", cat):
        return "INDUSTRIAL"
    if re.search(r"\b(?:hotel|banquet|guest\s*house|resort|restaurant|cafe|club|lounge)\b", n) or re.search(r"\b(?:hotel|hospitality)\b", cat):
        return "HOSPITALITY"
    if re.search(r"\b(?:office|showroom|shop|retail|commercial|sco|business\s+centre)\b", n) or re.search(r"\b(?:office|retail|commercial)\b", cat):
        return "COMMERCIAL"
    if re.search(r"\b(?:farm\s*land|farmland|agricultural|land|plot)\b", cat):
        return "LAND_OR_PLOT"
    if re.search(r"\b(?:residential|flat|apartment|floor|villa|house|bungalow)\b", cat):
        return "RESIDENTIAL"
    return base._asset_class(raw, category)

def analyze(row):
    a = base.analyze(dict(row))
    raw = str(legacy._row_get(row, "original_raw_text", "raw_text", "remarks", "description", "configuration") or "")
    listing = str(legacy._row_get(row, "listing_type", "lead_type", "transaction", "record_status") or "")
    category = str(legacy._row_get(row, "category", "property_type") or "")

    cls = _classify(raw, listing)
    asset = _asset_class(raw, category)
    tx = _transaction(raw, listing)

    a["predicted_class"] = cls
    a["predicted_asset_class"] = asset
    a["predicted_transaction"] = tx
    a["occupancy_status"] = _occupancy(raw, asset)
    return a

V2_FAILURE_CURRICULUM = [
    ("vacant_land_not_occupancy",
     {"source_id":"S1","listing_type":"Sale","category":"Land","locality":"Rajokri","area":"2","area_unit":"ACRE","price":"₹36 Cr","valid_mobiles":"9810202020","original_raw_text":"Rajokri vacant farmhouse land parcel 2 acres for outright sale at ₹36 Cr."},
     {"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN"}),
    ("wanted_buy_hotel",
     {"source_id":"S2","listing_type":"Wanted","category":"Hotel","locality":"Delhi NCR","area":"","area_unit":"","price":"Budget ₹80 Cr","valid_mobiles":"9810606060","original_raw_text":"Hospitality operator looking to buy operational 50-80 room hotel in Delhi NCR, budget up to ₹80 Cr."},
     {"class":"REQUIREMENT","transaction":"SALE","asset":"HOSPITALITY"}),
    ("requires_industrial_facility",
     {"source_id":"S3","listing_type":"Wanted","category":"Industrial","locality":"Manesar","area":"3","area_unit":"ACRE","price":"","valid_mobiles":"9820606060","original_raw_text":"Auto ancillary company requires 2-3 acre industrial facility on lease in Manesar."},
     {"class":"REQUIREMENT","transaction":"RENT","asset":"INDUSTRIAL"}),
    ("bungalow_plot_with_old_house",
     {"source_id":"S4","listing_type":"Sale","category":"Residential","locality":"Vasant Vihar","area":"800","area_unit":"SQYD","price":"₹31 Cr","valid_mobiles":"9820707070","original_raw_text":"Vasant Vihar bungalow plot 800 sq yds, old house, redevelopment opportunity, sale ₹31 Cr."},
     {"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT"}),
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

def _v1_regression_rows():
    return base.FAILURE_CURRICULUM

def score_training():
    errors=[]; total=0; correct=0

    for name,row,truth in V2_FAILURE_CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in truth.items():
            total += 1
            if p.get(field)==exp:
                correct += 1
            else:
                errors.append({"suite":"V2_FAILURE_LESSONS","name":name,"field":field,"expected":exp,"got":p.get(field)})

    for name,row,truth in _v1_regression_rows():
        p=_projection(analyze(row))
        for field,exp in truth.items():
            total += 1
            if p.get(field)==exp:
                correct += 1
            else:
                errors.append({"suite":"V1_FAILURE_REGRESSION","name":name,"field":field,"expected":exp,"got":p.get(field)})

    for name,row,exp_cls,exp_tx,exp_reason in legacy.CURRICULUM:
        p=_projection(analyze(row))
        for field,exp in (("class",exp_cls),("transaction",exp_tx)):
            total += 1
            if p.get(field)==exp:
                correct += 1
            else:
                errors.append({"suite":"LEGACY_511","name":name,"field":field,"expected":exp,"got":p.get(field)})
        total += 1
        if exp_reason in p["reasons"]:
            correct += 1
        else:
            errors.append({"suite":"LEGACY_511","name":name,"field":"lesson","expected":exp_reason,"got":p["reasons"]})

    return {
        "v2_failure_cases":len(V2_FAILURE_CURRICULUM),
        "v1_failure_regression_cases":len(_v1_regression_rows()),
        "legacy_cases":len(legacy.CURRICULUM),
        "checks":total,
        "correct":correct,
        "accuracy":round(100*correct/max(total,1),4),
        "errors":errors,
        "gate":"CHALLENGER_READY_FOR_FRESH_V3" if correct==total else "CHALLENGER_HOLD",
    }

def champion_hash():
    try:
        src=inspect.getsource(champion.predict_message)+inspect.getsource(champion.leading_demand_object)
        return hashlib.sha256(src.encode()).hexdigest()
    except Exception:
        return "UNAVAILABLE"

def self_check():
    if base.VERSION != BASE_VERSION:
        return {"status":"ERROR","error":f"Base Challenger changed: {base.VERSION}"}
    if legacy.VERSION != LEGACY_VERSION:
        return {"status":"ERROR","error":f"Legacy magazine student changed: {legacy.VERSION}"}
    if champion.VERSION != CHAMPION_VERSION:
        return {"status":"ERROR","error":f"Champion changed: {champion.VERSION}"}
    ch=champion_hash()
    if ch != CHAMPION_SHA256:
        return {"status":"ERROR","error":f"Champion hash changed: {ch}"}
    score=score_training()
    return {
        "version":VERSION,
        "mode":MODE,
        "status":"TRAINING_PASS" if score["gate"]=="CHALLENGER_READY_FOR_FRESH_V3" else "TRAINING_HOLD",
        "parent_challenger":{"version":base.VERSION,"immutable":True},
        "legacy_student":{"version":legacy.VERSION,"immutable":True},
        "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
        "training":score,
        "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,
                  "champion_mutations":0,"parent_challenger_mutations":0,"legacy_student_mutations":0},
    }
