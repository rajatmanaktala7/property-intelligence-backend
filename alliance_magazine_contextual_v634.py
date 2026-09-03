from __future__ import annotations
import base64, html, json, os, re, threading, time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from google.genai import types
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION="6.3.4-ALLIANCE-MAGAZINE-CONTEXTUAL-NEIGHBORHOOD-REPAIR"
MODE="LOCK_199_OF_210_FROM_631_GROUP_11_FAILURES_INTO_7_LISTINGS_FULL_PAGE_NEIGHBOR_CONTEXT_MULTI_READ_NO_FRESH_EXAM"

EXPECTED_EXAM="MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_FREEZE="ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC="5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_PARENT_VERSION="6.3.1-ALLIANCE-MAGAZINE-FAILURE-ONLY-FIELD-CHALLENGER-HISTORICAL-PARENT-PIN"
EXPECTED_LOCKED=199
EXPECTED_TOTAL=210
EXPECTED_REMAINING=11

STATE={"status":"NOT_STARTED","result":None,"phase":"WAITING","started_at":None,"finished_at":None,
       "listings_completed":0,"total_listings":7,"current_listing":None,"last_error":None}
_LOCK=threading.Lock(); _STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_contextual_v634_runs(
 run_id BIGSERIAL PRIMARY KEY,
 version TEXT NOT NULL,
 parent_version TEXT NOT NULL,
 source_exam_id TEXT NOT NULL,
 source_prediction_freeze_sha256 TEXT NOT NULL,
 locked_pass_checks INTEGER NOT NULL,
 repair_checks INTEGER NOT NULL,
 repaired_correct INTEGER NOT NULL,
 cumulative_correct INTEGER NOT NULL,
 cumulative_accuracy NUMERIC(8,4) NOT NULL,
 status TEXT NOT NULL,
 result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW())"""

ROW_PROMPT="""You are a forensic magazine transcription examiner. Read the WHOLE supplied page image.

TARGET PROPERTY REFERENCE: {ref}

Locate the exact inventory listing beginning with that printed reference. Use neighboring rows only to establish row boundaries.

Return JSON exactly:
{{
 "found":true|false,
 "reference_seen":"",
 "previous_reference":"",
 "next_reference":"",
 "raw_listing":"",
 "floor":"",
 "bedrooms":"",
 "price":"",
 "phones":[]
}}

Rules:
1. raw_listing must be the complete text belonging to TARGET PROPERTY REFERENCE only.
2. previous_reference and next_reference are boundary evidence only. Never copy their fields.
3. Preserve printed digits exactly.
4. floor: only explicit BMT/GF/FF/SF/TF/TERR tokens in target listing, joined by +.
5. bedrooms: number only, e.g. 2 or 4+1.
6. price: only explicit @price in target listing.
7. phones: only numbers owned by target listing/contact shown with that listing, never page footer/header.
8. If a target row wraps to a second printed line, include the continuation until the next property reference starts.
9. If uncertain, found=false. Do not guess.
"""

FIELD_PROMPT="""You are an independent visual verifier. Inspect the WHOLE supplied magazine page, not prior AI text.

TARGET PROPERTY REFERENCE: {ref}
FIELD TO VERIFY: {field}

Return JSON exactly:
{{
 "found":true|false,
 "reference_seen":"",
 "value":null,
 "evidence_text":"",
 "previous_reference":"",
 "next_reference":""
}}

Find the exact target listing first, establish its boundaries from the previous/next property references, then read ONLY the requested field.

Field rules:
- floor: explicit BMT/GF/FF/SF/TF/TERR tokens only, joined by +.
- bedrooms: numeric bedroom expression only.
- price: explicit @price only.
- phones: JSON list of every phone number belonging to this exact target listing. Preserve digits exactly and expand printed slash shorthand.
Never use broker office/footer numbers. Never borrow from adjacent property rows.
If the field cannot be read confidently from the image, found=false.
"""

def _engine(c): return getattr(c,"engine",None)
def _client(c): return getattr(c,"client",None)
def _app(c): return getattr(c,"app",None) or c
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False

def _norm_ref(x): return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())

def _json(resp):
    s=(resp.text or "").strip()
    s=re.sub(r"^```(?:json)?\s*","",s); s=re.sub(r"\s*```$","",s)
    return json.loads(s)

def _ask(client,img,prompt,model,tokens=3000):
    return _json(client.models.generate_content(
        model=model,
        contents=[prompt,types.Part.from_bytes(data=img,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(response_mime_type="application/json",temperature=0.0,max_output_tokens=tokens)
    ))

def _norm_phones(v):
    vals=v if isinstance(v,list) else [v] if v not in (None,"") else []
    out=[]
    for x in vals:
        s=re.sub(r"[\s-]","",str(x or ""))
        m=re.fullmatch(r"([6-9]\d{9})/(\d{1,4})",s)
        if m:
            b,suf=m.groups();out.extend([b,b[:-len(suf)]+suf]);continue
        if re.fullmatch(r"[6-9]\d{9}",s): out.append(s); continue
        if re.fullmatch(r"0\d{10}",s): out.append(s); continue
    return sorted(dict.fromkeys(out))

def _phones_from_raw(raw):
    s=str(raw or "")
    compact=re.sub(r"[\s-]","",s)
    out=[]
    for m in re.finditer(r"(?<!\d)([6-9]\d{9})/(\d{1,4})(?!\d)",compact):
        b,suf=m.groups();out.extend([b,b[:-len(suf)]+suf])
    for d in re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)",compact):out.append(d)
    for d in re.findall(r"(?<!\d)(0\d{10})(?!\d)",compact):out.append(d)
    return sorted(dict.fromkeys(out))

def _canon(field,v):
    if field=="phones":return _norm_phones(v)
    s=str(v or "").strip().upper().replace(" ","")
    if field=="bedrooms":
        m=re.search(r"(\d+(?:\+\d+)?)",s); return m.group(1) if m else ""
    if field=="floor":
        toks=re.findall(r"BMT|GF|FF|SF|TF|TERR",s);out=[]
        for t in toks:
            if t not in out:out.append(t)
        return "+".join(out)
    if field=="price":
        return s.replace("CRORES","CR").replace("CRORE","CR").replace("LAKHS","L").replace("LAKH","L").replace("LAC","L")
    return s

def _valid_target(ref,d):
    if not isinstance(d,dict) or not d.get("found"):return False
    nr=_norm_ref(ref)
    seen=_norm_ref(d.get("reference_seen"))
    raw=_norm_ref(d.get("raw_listing") or d.get("evidence_text"))
    # exact requested ref must be visible in explicit ref field or supporting image transcription.
    return nr==seen or nr in raw

def _row_read(client,img,ref,model,label):
    try:d=_ask(client,img,ROW_PROMPT.format(ref=ref),model,3500)
    except Exception:return None
    if not _valid_target(ref,d):return None
    raw=str(d.get("raw_listing") or "")
    phones=_norm_phones(d.get("phones"))
    raw_phones=_phones_from_raw(raw)
    # If model omitted phones but raw transcription contains exact valid phones, retain them.
    if not phones and raw_phones:phones=raw_phones
    return {
        "label":label,
        "reference_seen":str(d.get("reference_seen") or ""),
        "previous_reference":str(d.get("previous_reference") or ""),
        "next_reference":str(d.get("next_reference") or ""),
        "raw_listing":raw,
        "floor":_canon("floor",d.get("floor")),
        "bedrooms":_canon("bedrooms",d.get("bedrooms")),
        "price":_canon("price",d.get("price")),
        "phones":phones
    }

def _field_read(client,img,ref,field,model,label):
    try:d=_ask(client,img,FIELD_PROMPT.format(ref=ref,field=field),model,2200)
    except Exception:return None
    if not _valid_target(ref,d):return None
    val=_canon(field,d.get("value"))
    if val in ("",[]):return None
    return {"label":label,"value":val,"evidence":str(d.get("evidence_text") or ""),
            "previous_reference":str(d.get("previous_reference") or ""),
            "next_reference":str(d.get("next_reference") or "")}

def _consensus(values):
    if not values:return None,{"reason":"NO_CANDIDATES"}
    buckets=defaultdict(list)
    for item in values:
        k=json.dumps(item["value"],sort_keys=True)
        buckets[k].append(item)
    ranked=sorted(buckets.items(),key=lambda kv:(len(kv[1]),len(kv[0])),reverse=True)
    key,items=ranked[0]
    return json.loads(key),{
        "votes":len(items),
        "total_candidates":len(values),
        "methods":[i["label"] for i in items],
        "evidence":[i.get("evidence") or i.get("raw_listing") or "" for i in items[:5]],
        "previous_refs":[i.get("previous_reference","") for i in items[:5]],
        "next_refs":[i.get("next_reference","") for i in items[:5]]
    }

def inspect_listing(client,img,ref,fields,model):
    row_reads=[]
    # Independent complete-row passes exploit full-page context, the only locator family
    # that previously demonstrated reliable reference discovery on these pages.
    for i in range(4):
        r=_row_read(client,img,ref,model,f"ROW_FULL_{i+1}")
        if r:row_reads.append(r)

    out={}
    audit={"row_reads":row_reads}
    for field in fields:
        cands=[]
        for r in row_reads:
            val=r.get(field)
            if val not in ("",[]):
                cands.append({"label":r["label"],"value":val,"raw_listing":r["raw_listing"],
                              "previous_reference":r["previous_reference"],"next_reference":r["next_reference"]})
        # Add three independent field-specific visual verifications.
        for j in range(3):
            q=_field_read(client,img,ref,field,model,f"FIELD_VERIFY_{j+1}")
            if q:cands.append(q)
        value,meta=_consensus(cands)
        # Require at least 2 agreeing observations; singleton is evidence, not an accepted repair.
        accepted=value if value is not None and meta.get("votes",0)>=2 else None
        meta["accepted"]=accepted is not None
        out[field]={"value":accepted,"best_observed":value,"consensus":meta}
    return out,audit

def _load_parent(engine):
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT run_id,version,parent_version,source_exam_id,source_prediction_freeze_sha256,
                 locked_pass_checks,repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result,created_at
          FROM alliance_magazine_failure_only_v630_runs
          WHERE version=:v
            AND source_exam_id=:e
            AND source_prediction_freeze_sha256=:p
            AND cumulative_correct=:cc
            AND repaired_correct=5
            AND repair_checks=16
            AND status='TRAINING_HOLD'
          ORDER BY run_id ASC
        """),{"v":EXPECTED_PARENT_VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,"cc":EXPECTED_LOCKED}).all()
    for row in rows:
        d=dict(row._mapping);res=d.get("result") or {}
        repairs=((res.get("repair") or {}).get("repairs") or []) if isinstance(res,dict) else []
        remaining=[x for x in repairs if not x.get("passed")]
        if len(remaining)==EXPECTED_REMAINING:
            d["_remaining"]=remaining
            return d
    return None

def _truth_map():return {str(t["case_id"]):t for t in frozen_v2.TRUTH}

def _state():
    return {"version":VERSION,"mode":MODE,"status":STATE["status"],"phase":STATE["phase"],
            "started_at":STATE["started_at"],"finished_at":STATE["finished_at"],
            "listings_completed":STATE["listings_completed"],"total_listings":STATE["total_listings"],
            "current_listing":STATE["current_listing"],"last_error":STATE["last_error"],
            "result_ready":bool(STATE.get("result"))}

def run_once(core):
    if not _LOCK.acquire(False):return _state()
    try:
        STATE.update(status="RUNNING",phase="PIN_631_PARENT",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,listings_completed=0,current_listing=None,last_error=None)
        engine=_engine(core);client=_client(core)
        if engine is None or client is None:raise RuntimeError("Core engine/Gemini client unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:raise RuntimeError("Semantic student changed")
        with engine.begin() as c:c.execute(text(DDL))
        parent=_load_parent(engine)
        if not parent:raise RuntimeError("Exact 6.3.1 parent with 199/210 and 11 remaining failures not found")

        grouped=defaultdict(list)
        for e in parent["_remaining"]:
            grouped[(str(e["case_id"]),int(e["page"]),str(e["ref"]))].append(str(e["field"]))
        STATE["total_listings"]=len(grouped)

        truth=_truth_map();pages=frozen_v2.PAGE_IMAGES_B64
        model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
        repaired=[];correct=0
        STATE["phase"]="FULL_PAGE_CONTEXTUAL_NEIGHBORHOOD_REPAIR"

        for idx,((case_id,page,ref),fields) in enumerate(grouped.items(),1):
            STATE["current_listing"]=f"{case_id}:{ref}"
            img=base64.b64decode(pages[str(page)])
            pred,audit=inspect_listing(client,img,ref,fields,model)
            for field in fields:
                expected=_canon(field,truth[case_id].get(field))
                accepted=pred[field]["value"]
                got=_canon(field,accepted)
                passed=(accepted is not None and got==expected)
                if passed:correct+=1
                repaired.append({"case_id":case_id,"page":page,"ref":ref,"field":field,
                                 "expected":expected,"repaired":got,"passed":passed,
                                 "prediction":pred[field],"listing_audit":audit})
            STATE["listings_completed"]=idx

        cumulative=EXPECTED_LOCKED+correct
        acc=round(100*cumulative/EXPECTED_TOTAL,4)
        status="TRAINING_PASS" if correct==EXPECTED_REMAINING else "TRAINING_HOLD"
        result={
            "version":VERSION,"mode":MODE,"status":status,
            "parent":{"version":EXPECTED_PARENT_VERSION,"parent_run_id":parent["run_id"],
                      "locked_pass_checks":EXPECTED_LOCKED,"remaining_fields":EXPECTED_REMAINING,
                      "source_exam_id":EXPECTED_EXAM,"prediction_freeze_sha256":EXPECTED_FREEZE,
                      "preserved_immutable":True},
            "repair":{"unique_listings":len(grouped),"repair_checks":EXPECTED_REMAINING,
                      "repaired_correct":correct,"repair_accuracy":round(100*correct/EXPECTED_REMAINING,4),
                      "remaining_failures":EXPECTED_REMAINING-correct,"repairs":repaired},
            "cumulative_training_closure":{"correct_checks":cumulative,"total_checks":EXPECTED_TOTAL,"accuracy":acc,
                "scientific_note":"199 checks remain locked from 6.3.1. The 11 unresolved fields are grouped into 7 listings. Predictions come from full-page contextual row reads plus independent field verification. Truth is used only after predictions for grading. This is training, not fresh certification."},
            "lessons":{
                "v632":"Model-generated coordinates could not locate any of the 11 hard rows.",
                "v633":"Deterministic small-tile reference recognition also found none of the 11 hard rows.",
                "v634":"Return to the previously proven full-page reference-discovery regime, but add explicit neighbor-boundary ownership and multi-read field consensus."
            },
            "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3_CERTIFICATION" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_REMAINING_V634_FAILURES",
            "safety":{"fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                      "source_exam_mutations":0,"truth_mutations":0,"canonical_property_writes":0,
                      "canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0,
                      "semantic_student_mutations":0,"failed_v632_mutations":0,"failed_v633_mutations":0}
        }
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_contextual_v634_runs(
              version,parent_version,source_exam_id,source_prediction_freeze_sha256,locked_pass_checks,
              repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result)
              VALUES(:v,:pv,:e,:p,:l,:rc,:rco,:cc,:a,:s,CAST(:r AS JSONB))"""),
              {"v":VERSION,"pv":EXPECTED_PARENT_VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,
               "l":EXPECTED_LOCKED,"rc":EXPECTED_REMAINING,"rco":correct,"cc":cumulative,
               "a":acc,"s":status,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status=status,result=result,phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),current_listing=None)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",last_error=f"{type(exc).__name__}: {exc}",
                     finished_at=datetime.now(timezone.utc).isoformat(),current_listing=None)
        return _state()
    finally:_LOCK.release()

def status(core):
    if STATE.get("result"):return STATE["result"]
    return _state()

def dashboard(core):
    s=status(core);r=s.get("repair") or {};c=s.get("cumulative_training_closure") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Contextual Repair 6.3.4</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1400px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Contextual Neighborhood Repair 6.3.4</b><br><small>Lock 199/210 · 7 hard listings · full-page neighbor ownership · no fresh pages</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Listings {html.escape(str(s.get("listings_completed")))} / {html.escape(str(s.get("total_listings")))} · Current {html.escape(str(s.get("current_listing")))}<br>
Repair accuracy {html.escape(str(r.get("repair_accuracy")))}% · Cumulative {html.escape(str(c.get("correct_checks")))} / {html.escape(str(c.get("total_checks")))} ({html.escape(str(c.get("accuracy")))}%)</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-contextual-v634/status"):
        @app.get("/api/property-brain/magazine-contextual-v634/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/magazine-contextual-v634"):
        @app.get("/property-brain/magazine-contextual-v634",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V634_DELAY","55")));run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-contextual-v634").start()
    return STATE
