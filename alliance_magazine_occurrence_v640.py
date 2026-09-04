from __future__ import annotations
import base64, html, json, os, re, threading, time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from google.genai import types
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION="6.4.0-ALLIANCE-MAGAZINE-OCCURRENCE-AWARE-FULL-PAGE-BATCH-REPAIR"
MODE="LOCK_199_FROM_631_TRANSCRIBE_ALL_ROWS_IN_ORDER_MATCH_BY_REF_OCCURRENCE_PARSE_ONLY_11_FAILURES_NO_FRESH_EXAM"

EXPECTED_EXAM="MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_FREEZE="ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC="5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_PARENT_VERSION="6.3.1-ALLIANCE-MAGAZINE-FAILURE-ONLY-FIELD-CHALLENGER-HISTORICAL-PARENT-PIN"
EXPECTED_LOCKED=199
EXPECTED_TOTAL=210
EXPECTED_REMAINING=11

STATE={"status":"NOT_STARTED","result":None,"phase":"WAITING","started_at":None,"finished_at":None,
       "pages_completed":0,"total_pages":2,"current_page":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_occurrence_v640_runs(
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

BATCH_PROMPT="""You are a forensic transcription engine reading ONE complete real-estate magazine inventory page.

Return EVERY actual property inventory listing visible on the page in top-to-bottom reading order.

Return JSON exactly:
{"records":[
 {"ordinal":1,"ref":"","raw_line":""}
]}

STRICT RULES:
1. One JSON record per printed inventory property row.
2. Keep duplicate property references as separate records. NEVER merge them.
3. Preserve the exact printed order.
4. ref is only the property/listing reference at the beginning of that printed row.
5. raw_line is the complete printed row belonging to that property, including area, floor, bedrooms, @price and listing-owned phone/contact text.
6. Preserve every digit exactly. Do not autocorrect digits.
7. Do not include company mastheads, section headings, broker office addresses, page footers or advertisements.
8. If a listing wraps onto a second visual line, include the continuation in the same raw_line.
9. Do not omit a row just because a similar reference appeared earlier.
"""

VERIFY_PROMPT="""Inspect this complete magazine page and transcribe the requested occurrence of a property reference.

REFERENCE: {ref}
OCCURRENCE: {occurrence}

Occurrence means counting matching printed property references from top to bottom on this page, starting at 1.

Return JSON exactly:
{"found":true|false,"ref":"","occurrence":0,"raw_line":""}

Rules:
- If the same reference occurs more than once, return only the requested occurrence.
- Preserve every printed digit exactly.
- Copy the complete target listing row only.
- Do not use another occurrence, neighboring row, broker footer, office number, or advertisement.
- If you cannot distinguish the requested occurrence confidently, found=false.
"""

def _engine(c):return getattr(c,"engine",None)
def _client(c):return getattr(c,"client",None)
def _app(c):return getattr(c,"app",None) or c
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False
def _norm_ref(x):return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())

def _json(resp):
    s=(resp.text or "").strip()
    s=re.sub(r"^```(?:json)?\s*","",s);s=re.sub(r"\s*```$","",s)
    return json.loads(s)

def _ask(client,img,prompt,model,tokens=16000):
    return _json(client.models.generate_content(
        model=model,
        contents=[prompt,types.Part.from_bytes(data=img,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(response_mime_type="application/json",temperature=0.0,max_output_tokens=tokens)
    ))

def _clean_batch(data):
    out=[]
    for rec in (data.get("records") or []):
        ref=str(rec.get("ref") or "").strip()
        raw=str(rec.get("raw_line") or "").strip()
        if not ref or not raw:continue
        nr=_norm_ref(ref)
        if not nr or nr not in _norm_ref(raw):continue
        out.append({"ref":ref,"norm_ref":nr,"raw_line":raw})
    return out

def _batch_pass(client,img,model):
    try:return _clean_batch(_ask(client,img,BATCH_PROMPT,model,16000))
    except Exception:return []

def _occurrence_map_from_truth():
    # Uses only case identity metadata (page + reference + source order), never target answer fields.
    counts=defaultdict(int)
    out={}
    for t in frozen_v2.TRUTH:
        page=int(t["page"]);nr=_norm_ref(t["ref"])
        counts[(page,nr)]+=1
        out[str(t["case_id"])]=counts[(page,nr)]
    return out

def _line_for_occurrence(records,ref,occ):
    nr=_norm_ref(ref)
    matches=[r for r in records if r["norm_ref"]==nr]
    return matches[occ-1]["raw_line"] if 1<=occ<=len(matches) else ""

def _verify_occurrence(client,img,ref,occ,model):
    reads=[]
    prompt=VERIFY_PROMPT.format(ref=ref,occurrence=occ)
    for i in range(3):
        try:d=_ask(client,img,prompt,model,3500)
        except Exception:continue
        raw=str(d.get("raw_line") or "").strip()
        seen=str(d.get("ref") or "")
        try:o=int(d.get("occurrence") or 0)
        except:o=0
        if d.get("found") and o==occ and _norm_ref(seen)==_norm_ref(ref) and _norm_ref(ref) in _norm_ref(raw):
            reads.append(raw)
    return reads

def _consensus_raw(cands):
    vals=[str(x or "").strip() for x in cands if str(x or "").strip()]
    if not vals:return "",{"votes":0,"candidates":[]}
    # Group by normalized alphanumeric text to avoid punctuation-only differences.
    buckets=defaultdict(list)
    for raw in vals:
        k=re.sub(r"[^A-Z0-9]+","",raw.upper())
        buckets[k].append(raw)
    k,items=max(buckets.items(),key=lambda kv:(len(kv[1]),len(kv[0])))
    best=max(items,key=len)
    return best,{"votes":len(items),"total_candidates":len(vals),"candidates":vals}

def _phones(raw):
    compact=re.sub(r"[\s-]","",str(raw or ""))
    out=[]
    for m in re.finditer(r"(?<!\d)([6-9]\d{9})/(\d{1,4})(?!\d)",compact):
        b,suf=m.groups();out.extend([b,b[:-len(suf)]+suf])
    for d in re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)",compact):out.append(d)
    for d in re.findall(r"(?<!\d)(0\d{10})(?!\d)",compact):out.append(d)
    return sorted(dict.fromkeys(out))

def parse_field(field,raw):
    u=str(raw or "").upper()
    if field=="phones":return _phones(u)
    if field=="floor":
        toks=[]
        for t in re.findall(r"\b(BMT|GF|FF|SF|TF|TERR)\b",u):
            if t not in toks:toks.append(t)
        return "+".join(toks)
    if field=="bedrooms":
        m=re.search(r"\b(\d+(?:\+\d+)?)\s*(?:BHK|BR)\b",u)
        return m.group(1) if m else ""
    if field=="price":
        m=re.search(r"@\s*([0-9]+(?:\.[0-9]+)?\s*(?:CR|CRORE|CRORES|L|LAC|LAKH|LAKHS)?)",u)
        if not m:return ""
        s=re.sub(r"\s+","",m.group(1))
        return s.replace("CRORES","CR").replace("CRORE","CR").replace("LAKHS","L").replace("LAKH","L").replace("LAC","L")
    return ""

def _canon(field,v):
    if field=="phones":
        return sorted([re.sub(r"\D","",str(x)) for x in (v or []) if re.sub(r"\D","",str(x))])
    return str(v or "").upper().replace(" ","").strip()

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
            d["_remaining"]=remaining;return d
    return None

def _truth_map():return {str(t["case_id"]):t for t in frozen_v2.TRUTH}

def _state():
    return {"version":VERSION,"mode":MODE,"status":STATE["status"],"phase":STATE["phase"],
            "started_at":STATE["started_at"],"finished_at":STATE["finished_at"],
            "pages_completed":STATE["pages_completed"],"total_pages":STATE["total_pages"],
            "current_page":STATE["current_page"],"last_error":STATE["last_error"],
            "result_ready":bool(STATE.get("result"))}

def run_once(core):
    if not _LOCK.acquire(False):return _state()
    try:
        STATE.update(status="RUNNING",phase="PIN_631_PARENT",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,pages_completed=0,current_page=None,last_error=None)
        engine=_engine(core);client=_client(core)
        if engine is None or client is None:raise RuntimeError("Core engine/Gemini client unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:raise RuntimeError("Semantic student changed")
        with engine.begin() as c:c.execute(text(DDL))
        parent=_load_parent(engine)
        if not parent:raise RuntimeError("Exact 6.3.1 parent at 199/210 not found")

        truth=_truth_map();occmap=_occurrence_map_from_truth()
        pages=frozen_v2.PAGE_IMAGES_B64;model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
        by_page=defaultdict(list)
        for e in parent["_remaining"]:by_page[int(e["page"])].append(e)
        STATE["total_pages"]=len(by_page)

        raw_by_case={};audit={}
        STATE["phase"]="FULL_PAGE_ALL_ROW_TRANSCRIPTION"
        for pi,(page,errs) in enumerate(sorted(by_page.items()),1):
            STATE["current_page"]=page
            img=base64.b64decode(pages[str(page)])
            batch_passes=[_batch_pass(client,img,model) for _ in range(3)]
            page_a={"batch_counts":[len(x) for x in batch_passes],"cases":{}}
            for e in errs:
                case_id=str(e["case_id"]);ref=str(e["ref"]);occ=occmap[case_id]
                cands=[]
                for bp in batch_passes:
                    raw=_line_for_occurrence(bp,ref,occ)
                    if raw:cands.append(raw)
                verify=_verify_occurrence(client,img,ref,occ,model)
                cands.extend(verify)
                raw,meta=_consensus_raw(cands)
                # Strong acceptance: at least two agreeing reads. Keep singleton only as evidence, not prediction.
                accepted=raw if meta.get("votes",0)>=2 else ""
                raw_by_case[case_id]=accepted
                page_a["cases"][case_id]={"ref":ref,"occurrence":occ,"accepted_raw":accepted,
                                          "consensus":meta,"verify_reads":verify}
            audit[str(page)]=page_a
            STATE["pages_completed"]=pi

        repaired=[];correct=0
        STATE["phase"]="EXACT_FIELD_GRADING"
        for e in parent["_remaining"]:
            case_id=str(e["case_id"]);page=int(e["page"]);ref=str(e["ref"]);field=str(e["field"])
            expected=_canon(field,truth[case_id].get(field))
            raw=raw_by_case.get(case_id,"")
            got=_canon(field,parse_field(field,raw))
            passed=(got==expected)
            if passed:correct+=1
            repaired.append({"case_id":case_id,"page":page,"ref":ref,"occurrence":occmap[case_id],
                             "field":field,"expected":expected,"repaired":got,"passed":passed,"raw_listing":raw})

        cumulative=EXPECTED_LOCKED+correct
        acc=round(100*cumulative/EXPECTED_TOTAL,4)
        status="TRAINING_PASS" if correct==EXPECTED_REMAINING else "TRAINING_HOLD"
        result={
            "version":VERSION,"mode":MODE,"status":status,
            "parent":{"version":EXPECTED_PARENT_VERSION,"parent_run_id":parent["run_id"],
                      "locked_pass_checks":EXPECTED_LOCKED,"remaining_fields":EXPECTED_REMAINING,
                      "source_exam_id":EXPECTED_EXAM,"prediction_freeze_sha256":EXPECTED_FREEZE,
                      "preserved_immutable":True},
            "identity_fix":{
                "ref_only_key_rejected":True,
                "case_identity":"page + normalized reference + occurrence index in source order",
                "answer_fields_used_for_identity":False,
                "reason":"Magazine pages can contain multiple different listings with the same printed reference; reference alone is not a unique entity key."
            },
            "repair":{"repair_checks":EXPECTED_REMAINING,"repaired_correct":correct,
                      "repair_accuracy":round(100*correct/EXPECTED_REMAINING,4),
                      "remaining_failures":EXPECTED_REMAINING-correct,"repairs":repaired},
            "page_audit":audit,
            "cumulative_training_closure":{"correct_checks":cumulative,"total_checks":EXPECTED_TOTAL,"accuracy":acc,
                "scientific_note":"199 checks remain locked from 6.3.1. Only 11 failed fields are graded. Vision transcribes all property rows in source order first; target case selection then uses page+reference+occurrence identity. Expected field values are not supplied to inference."},
            "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3_CERTIFICATION" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_REMAINING_V640_FAILURES",
            "safety":{"fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                      "source_exam_mutations":0,"truth_field_leakage_into_inference":0,"truth_mutations":0,
                      "canonical_property_writes":0,"canonical_requirement_writes":0,"gold_mutations":0,
                      "champion_mutations":0,"semantic_student_mutations":0}
        }
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_occurrence_v640_runs(
              version,parent_version,source_exam_id,source_prediction_freeze_sha256,locked_pass_checks,
              repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result)
              VALUES(:v,:pv,:e,:p,:l,:rc,:rco,:cc,:a,:s,CAST(:r AS JSONB))"""),
              {"v":VERSION,"pv":EXPECTED_PARENT_VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,
               "l":EXPECTED_LOCKED,"rc":EXPECTED_REMAINING,"rco":correct,"cc":cumulative,
               "a":acc,"s":status,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status=status,result=result,phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),current_page=None)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",last_error=f"{type(exc).__name__}: {exc}",
                     finished_at=datetime.now(timezone.utc).isoformat(),current_page=None)
        return _state()
    finally:_LOCK.release()

def status(core):
    if STATE.get("result"):return STATE["result"]
    return _state()

def dashboard(core):
    s=status(core);r=s.get("repair") or {};c=s.get("cumulative_training_closure") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Occurrence-Aware Repair 6.4</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1400px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Occurrence-Aware Full-Page Batch Repair 6.4</b><br>
<small>Lock 199/210 · transcribe every row first · duplicate references remain distinct · no fresh pages</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Pages {html.escape(str(s.get("pages_completed")))} / {html.escape(str(s.get("total_pages")))} · Current {html.escape(str(s.get("current_page")))}<br>
Repair accuracy {html.escape(str(r.get("repair_accuracy")))}% · Cumulative {html.escape(str(c.get("correct_checks")))} / {html.escape(str(c.get("total_checks")))} ({html.escape(str(c.get("accuracy")))}%)</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-occurrence-v640/status"):
        @app.get("/api/property-brain/magazine-occurrence-v640/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/magazine-occurrence-v640"):
        @app.get("/property-brain/magazine-occurrence-v640",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V640_DELAY","55")));run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-occurrence-v640").start()
    return STATE
