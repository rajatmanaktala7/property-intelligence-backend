from __future__ import annotations

import html
import json
import re
from fastapi.responses import HTMLResponse

import alliance_property_brain_foundation_v1 as foundation
import alliance_autonomous_student_v430 as v430
import alliance_autonomous_student_v434 as v434

VERSION = "4.2.6-ALLIANCE-AUTOMATED-TRUTH-INTEGRITY-AUDITOR"
MODE = "READ_ONLY_PSEUDO_TRUTH_CONTRADICTION_AUDIT_NO_STUDENT_TUNING_NO_GOLD_MUTATION"

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core
def _norm(s): return re.sub(r"\s+", " ", (s or "").lower()).strip()

def _strip_leading_symbols(s):
    return re.sub(r"^[^a-z0-9]+", "", _norm(s))

def demand_contract(raw):
    n=_strip_leading_symbols(raw)
    demand=bool(re.search(
        r"^(?:urgent(?:ly)?\s+)?(?:required?|requirement|need(?:ed)?|wanted|looking\s+for|seeking)\b|"
        r"\b(?:client|tenant|buyer)\s+(?:requires?|needs?|wants?|seeks?)\b", n))
    asset=bool(re.search(
        r"\b(?:bhk|flat|apartment|farm\s*house|house|hotel|guest\s*house|hostel|dormitory|"
        r"property|building|plot|shop|office|villa|floor|showroom|warehouse)s?\b", n))
    availability=bool(re.search(
        r"^(?:available|avl|for\s+sale|for\s+rent|to[- ]?let)\b|"
        r"\b(?:asking\s+(?:rent|price)|owner\s+wants\s+to\s+sell|deal\s+available|"
        r"getting\s+vacated|ready\s+to\s+move)\b", n))
    # "if you have ... available" is a solicitation object, not an owned offer.
    solicitation=bool(re.search(r"\bif\s+you\s+have\b.{0,120}\bavailable\b", n))
    return demand and asset and (not availability or solicitation)

def rental_contract(raw):
    n=_strip_leading_symbols(raw)
    return bool(
        re.search(r"\brental\s+requirement\b", n) or
        re.search(r"\b(?:require|requires|required|need|needed|wanted|looking\s+for)\b.{0,90}\b(?:for\s+rent|on\s+rent|to\s+rent)\b", n)
    )

def sale_contract(raw):
    n=_strip_leading_symbols(raw)
    return bool(re.search(
        r"\b(?:want(?:s|ed)?|need(?:s|ed)?|require(?:s|d)?|looking\s+to|seeking\s+to)\b"
        r".{0,80}\b(?:purchase|buy|acquire)\b", n))

def revised_truth(engine):
    rows=v430._v4_truth_rows(engine)
    out=[]
    for item in rows:
        x=item["row"]; raw=x["raw_text"]
        old={"class":item["truth"][0],"transaction":item["truth"][1],"ownership":item["truth"][2]}
        new=dict(old); reasons=[]
        dc=demand_contract(raw); rc=rental_contract(raw); sc=sale_contract(raw)

        # Generic truth-integrity correction: explicit demand grammar owns the message.
        # Do not let a solicitation phrase ("if you have ... available") become supply.
        if dc and old["class"]=="PROPERTY_AVAILABILITY":
            new["class"]="REQUIREMENT"
            reasons.append("DEMAND_CONTRACT_OVERRIDES_SOLICITATION_AVAILABILITY")

        # Explicit rental requirement is transaction RENT. This is direct ontology evidence.
        if dc and rc and new["transaction"]!="RENT":
            new["transaction"]="RENT"
            reasons.append("EXPLICIT_RENTAL_REQUIREMENT")

        if dc and sc and new["transaction"]!="SALE":
            new["transaction"]="SALE"
            reasons.append("EXPLICIT_ACQUISITION_REQUIREMENT")

        out.append({
            "ordinal":x["ordinal"],"audit_id":str(x["audit_id"]),"raw_text":raw,
            "old_truth":old,"revised_truth":new,"changed":old!=new,
            "truth_source":item.get("truth_source"),"reasons":reasons,
            "signals":{"demand_contract":dc,"rental_contract":rc,"sale_contract":sc},
        })
    return out

def report(engine):
    rows=revised_truth(engine)
    changed=[x for x in rows if x["changed"]]
    return {
        "version":VERSION,"mode":MODE,"cases":len(rows),"truth_revisions":len(changed),
        "revisions":changed,
        "policy":"Versioned pseudo-truth correction only. Original V4 truth tables remain immutable. Gold is untouched.",
        "safety":{"student_tuning":0,"gold_mutations":0,"production_writes":0,"whatsapp_writes":0},
    }

def _dashboard(engine):
    s=report(engine)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Truth Integrity 4.2.6</title>
<style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1200px}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:18px;border-radius:10px}}.ok{{padding:15px;background:#e8f8ee;border-radius:10px;font-weight:700}}</style></head><body>
<h1>Alliance Automated Truth Integrity Auditor 4.2.6</h1>
<div class='ok'>Original V4 truth remains immutable. This is a versioned correction layer for contradictory pseudo-truth only.</div>
<pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core)
    if not foundation._route_exists(app,"/api/property-brain/truth-integrity-v426/status"):
        @app.get("/api/property-brain/truth-integrity-v426/status")
        def status_v426(): return report(engine)
    if not foundation._route_exists(app,"/property-brain/truth-integrity-v426"):
        @app.get("/property-brain/truth-integrity-v426",response_class=HTMLResponse)
        def page_v426(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/truth-integrity-v426"}
