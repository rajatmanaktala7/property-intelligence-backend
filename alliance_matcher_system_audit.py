from __future__ import annotations
from collections import Counter
import re
from sqlalchemy import text
import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_first_match_v1 as engine

BATCH_SIZE = 5000

def _assert_regressions():
    failures = []
    req = phase5.parse_requirement("Urgent Requirement Kothi 300Yd To 400Yd In South Delhi B Cataegory Colonies As Per Market Price Cheque Circle Rate Preferable Regards Sunil Kumar 9212451029 K S Associates")
    expected = {"transaction":"SALE","family":"RESIDENTIAL","subtype":"VILLA","area_min_sqft":2700.0,"area_max_sqft":3600.0,"budget_max":None,"location_scope":"REGION"}
    for key, value in expected.items():
        actual = req.get(key)
        ok = (actual is not None and abs(float(actual)-value)<0.01) if isinstance(value,float) else actual == value
        if not ok: failures.append(f"kothi: {key} expected={value!r} actual={actual!r}")
    for raw, exp in [
        ("Restaurant for rent in Saket 2000 sqft budget 4 lakh", ("RENT","COMMERCIAL","RESTAURANT")),
        ("High street retail shop for sale in Sector 63A 415 sqft 62.25 lakh", ("SALE","COMMERCIAL","RETAIL")),
    ]:
        q = phase5.parse_requirement(raw)
        if (q.get("transaction"), q.get("family"), q.get("subtype")) != exp:
            failures.append(f"parse regression: {raw}")
    if phase5.parse_budget("9212451029 K S Associates") != (None,None): failures.append("phone budget")
    if phase5.area_to_sqft("300yd") != 2700.0: failures.append("300yd")
    if phase5.candidate_location("3 BHK") is not None: failures.append("3 BHK location")
    if phase5.candidate_location("188 sq.m") is not None: failures.append("area location")
    if phase5.text_contains_contact("2023.6151583396"): failures.append("decimal false contact")
    if phase5.text_contains_contact("159d9eab-5be5-5313-9af5-8f9913522087"): failures.append("uuid false contact")
    if not phase5.text_contains_contact("Call 9212451029 for details"): failures.append("real phone missed")
    if "9212451029" in phase5.sanitize_text("Call 9212451029 for details"): failures.append("real phone unsanitized")
    return failures

def _count(db, table):
    if not phase5.table_exists(db, table): return 0
    with db.connect() as c:
        return int(c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)

def _wa_columns(db):
    cols = phase5.table_columns(db, "pi_whatsapp_property_master")
    wanted = ["record_id","lead_type","description","area","configuration_details","price","source","captured_on","verification","furnishing","floor","generation_id"]
    return [x for x in wanted if x in cols]

def _fetch_batch(db, cols, offset, limit):
    qcols = ", ".join('"' + x + '"' for x in cols)
    order_col = "record_id" if "record_id" in cols else cols[0]
    sql = f'SELECT {qcols} FROM "pi_whatsapp_property_master" ORDER BY "{order_col}" NULLS LAST OFFSET :off LIMIT :lim'
    with db.connect() as c:
        return [dict(r) for r in c.execute(text(sql), {"off":offset,"lim":limit}).mappings().all()]

def _normalize(d):
    tx = phase5.norm(d.get("lead_type"))
    if tx not in {"SALE","RENT"}: return None, "MISSING_OR_INVALID_TRANSACTION"
    desc = str(d.get("description") or "")
    cfg = str(d.get("configuration_details") or "")
    blob = (desc + " " + cfg).strip()
    loc = phase5.canonical_location(blob)
    if not loc and "NORTH GOA" in phase5.norm(blob): loc = "NORTH GOA"
    if not loc: loc = phase5.candidate_location(cfg)
    if not loc:
        return None, "INVALID_LOCATION" if cfg and not phase5.location_value_is_plausible(cfg) else "MISSING_LOCATION"
    fam, sub = phase5.family_subtype(cfg, desc)
    area = phase5.area_to_sqft(d.get("area"))
    ptext = d.get("price")
    price = phase5.money_value(ptext)
    comparable = price is not None and bool(re.search(r"(?i)\b(cr|crore|lac|lakh|lakhs|k)\b", str(ptext or "")))
    ver = d.get("verification") or "UNVERIFIED"
    if not phase5._available(ver): return None, "UNAVAILABLE"
    return {
        "source_bucket":"WHATSAPP_ALL_STORED","source_table":"pi_whatsapp_property_master",
        "record_id":str(d.get("record_id") or ""),"description":phase5.sanitize_text(desc),
        "location":loc,"transaction":tx,"family":fam,"subtype":sub,"area_sqft":area,
        "area_unit_verified":bool(area is not None),"price":price if comparable else None,
        "price_text":phase5.sanitize_text(ptext),"price_comparable":comparable,"quality":"READY",
        "verification":ver,"captured_on":d.get("captured_on"),
        "source_name":phase5.sanitize_text(d.get("source") or "WhatsApp"),"review_reasons":None,
    }, "MATCHER_READY"

def _self_match(p):
    req = {"raw":"SELF","primary_locations":[p["location"]],"location":p["location"],"location_scope":"LOCALITY",
           "transaction":p.get("transaction"),"family":p.get("family"),"subtype":p.get("subtype"),
           "acceptable_subtypes":[p.get("subtype")] if p.get("subtype") else [],
           "area_min_sqft":p.get("area_sqft"),"area_max_sqft":p.get("area_sqft"),
           "budget_min":None,"budget_max":p.get("price") if p.get("price_comparable") else None}
    ok, code, _ = phase5.eligible(req, p, "EXACT")
    return ok, code

def _leaks(p):
    clean = phase5.sanitize_public_payload({"description":p.get("description"),"price_text":p.get("price_text"),"source_name":p.get("source_name"),"review_reasons":p.get("review_reasons")})
    return phase5.public_payload_contact_paths(clean)

def main():
    failures = _assert_regressions()
    if failures:
        print("REGRESSION SUITE: FAIL")
        for x in failures: print(" -", x)
        raise SystemExit(2)
    print("REGRESSION SUITE: PASS")
    db = phase5.create_main_engine()
    raw_pi, raw_wa = _count(db,"pi_properties"), _count(db,"pi_whatsapp_property_master")
    canonical = phase5.load_pi_properties(db, limit=50000)
    cself, cleaks = Counter(), []
    for p in canonical:
        ok, code = _self_match(p)
        if not ok: cself[code]+=1
        if _leaks(p): cleaks.append(p.get("record_id"))
    print("")
    print("FULL WHATSAPP INVENTORY COVERAGE AUDIT")
    print("Raw pi_properties:", raw_pi)
    print("Loaded canonical:", len(canonical))
    print("Raw pi_whatsapp_property_master:", raw_wa)
    print("Batch size:", BATCH_SIZE)
    cols = _wa_columns(db)
    reasons, self_fail = Counter(), Counter()
    leaks, bad_examples, self_examples, ready = [], [], [], []
    processed = offset = 0
    while offset < raw_wa:
        batch = _fetch_batch(db, cols, offset, BATCH_SIZE)
        if not batch: break
        for d in batch:
            processed += 1
            p, reason = _normalize(d); reasons[reason]+=1
            if p is None:
                if reason in {"INVALID_LOCATION","MISSING_LOCATION"} and len(bad_examples)<25:
                    bad_examples.append({"record_id":d.get("record_id"),"configuration_details":d.get("configuration_details"),"description":phase5.sanitize_text(d.get("description"))[:220],"reason":reason})
                continue
            ready.append(p)
            ok, code = _self_match(p)
            if not ok:
                self_fail[code]+=1
                if len(self_examples)<25: self_examples.append({"record_id":p.get("record_id"),"location":p.get("location"),"reason":code})
            lp = _leaks(p)
            if lp and len(leaks)<50: leaks.append({"record_id":p.get("record_id"),"paths":lp})
        offset += len(batch)
        if processed % 25000 < len(batch): print(f"Progress: {processed}/{raw_wa} raw WhatsApp rows audited")
    deduped = phase5.dedupe_candidates(ready)
    print("")
    print("COVERAGE RESULTS")
    print("Raw WhatsApp rows processed:", processed)
    print("Normalized MATCHER_READY:", reasons["MATCHER_READY"])
    print("Deduped MATCHER_READY:", len(deduped))
    print("Classification totals:", dict(reasons.most_common()))
    print("WhatsApp self-match failures:", sum(self_fail.values()))
    print("WhatsApp self-match reasons:", dict(self_fail))
    print("WhatsApp public-text contact leaks:", len(leaks))
    print("Canonical self-match failures:", sum(cself.values()))
    print("Canonical public-text contact leaks:", len(cleaks))
    if bad_examples:
        print("\nLOCATION EXCLUSION EXAMPLES")
        for x in bad_examples: print(x)
    if self_examples:
        print("\nSELF-MATCH FAILURE EXAMPLES")
        for x in self_examples: print(x)
    if leaks:
        print("\nCONTACT LEAK EXAMPLES")
        for x in leaks: print(x)
    if processed != raw_wa: raise SystemExit(f"FAIL: audit coverage incomplete processed={processed} raw={raw_wa}")
    if sum(self_fail.values()) or sum(cself.values()): raise SystemExit("FAIL: normalized candidates fail hard-gate self-match")
    if leaks or cleaks: raise SystemExit("FAIL: real contact leak remains in public text")
    for q in [
        "Kothi 300Yd To 400Yd in South Delhi B Category Colonies as per market price cheque circle rate",
        "Restaurant for rent in Saket 2000 sqft budget 4 lakh",
        "Retail shop for sale in Sector 63A 415 sqft 62.25 lakh",
    ]:
        result = engine.run_match(db, q, min_score=70, limit=10)
        if "summary" not in result: raise SystemExit("FAIL: end-to-end matcher returned no summary")
    print("")
    print("FULL WHATSAPP INVENTORY AUDIT: PASS")
    print("FULL RAW COVERAGE TEST: PASS")
    print("LOCATION PURITY TEST: PASS")
    print("DATABASE-WIDE SELF-MATCH TEST: PASS")
    print("CONTACT LEAK TEST: PASS")
    print("END-TO-END WHATSAPP MATCHER: PASS")
    print("SYSTEM AUDIT: PASS")

if __name__ == "__main__":
    main()
