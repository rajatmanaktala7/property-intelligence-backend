from __future__ import annotations
import base64, html, io, json, os, re, threading, time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from fastapi.responses import HTMLResponse
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION="6.3.5-ALLIANCE-MAGAZINE-LINE-STRIP-SEGMENTATION-REPAIR"
MODE="LOCK_199_FROM_631_DETERMINISTIC_HORIZONTAL_TEXT_LINE_SEGMENTATION_CONTACT_SHEET_EXACT_STRIP_VERIFY_NO_FRESH_EXAM"

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

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_line_strip_v635_runs(
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

SHEET_PROMPT="""This image is a contact sheet made from individual horizontal text lines of ONE real-estate magazine page.
Each line is labeled L000, L001, etc. The labels are synthetic and not part of the magazine.

TARGET PROPERTY REFERENCES:
{refs}

Return JSON exactly:
{{"records":[{{"ref":"","line_id":"","raw_line":""}}]}}

For each target reference you can visibly find:
- identify the one line_id containing that exact property listing reference;
- copy the complete printed listing line exactly into raw_line;
- preserve every digit and punctuation relevant to area/floor/BHK/price/phones.
Do not use broker headers, office/footer addresses, or adjacent property rows.
Omit a target rather than guessing.
"""

VERIFY_PROMPT="""This image contains ONE enlarged horizontal line from a real-estate magazine.

TARGET REFERENCE: {ref}

Return JSON exactly:
{{"found":true|false,"ref":"","raw_line":""}}

found=true only if the exact target property reference is visibly printed in this line.
If found, transcribe the entire printed line exactly, preserving all digits.
Do not infer or correct digits.
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

def _ask(client,img,prompt,model,tokens=5000):
    return _json(client.models.generate_content(
        model=model,
        contents=[prompt,types.Part.from_bytes(data=img,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(response_mime_type="application/json",temperature=0.0,max_output_tokens=tokens)
    ))

def _jpeg(im):
    b=io.BytesIO();im.save(b,format="JPEG",quality=99);return b.getvalue()

def _right_text_boundary(gray):
    a=np.asarray(gray)
    h,w=a.shape
    ink=a<165
    y0=max(0,int(h*.14));y1=min(h,int(h*.91))
    dens=ink[y0:y1].mean(axis=0)
    # Dense vertical advertisement on far right: cut before it.
    far=dens[int(w*.72):]
    active=np.where(far>.18)[0]
    if len(active)>=20:
        first=int(w*.72)+int(active.min())
        return max(int(w*.60),first-22)
    return w-38

def segment_lines(img_bytes):
    im=Image.open(io.BytesIO(img_bytes)).convert("RGB")
    gray=im.convert("L")
    a=np.asarray(gray)
    h,w=a.shape
    x0=max(35,int(w*.045));x1=_right_text_boundary(gray)
    y0=max(145,int(h*.125));y1=min(h-90,int(h*.925))
    ink=a<170
    proj=ink[y0:y1,x0:x1].sum(axis=1)
    mask=proj>12

    raw=[];s=None
    for i,v in enumerate(mask):
        if v and s is None:s=i
        if s is not None and (not v or i==len(mask)-1):
            e=i if not v else i+1
            height=e-s
            if 3<=height<=20:raw.append((y0+s,y0+e))
            s=None

    merged=[]
    for s,e in raw:
        if merged and s-merged[-1][1]<=2:
            merged[-1]=(merged[-1][0],e)
        else:merged.append((s,e))

    strips=[]
    for idx,(s,e) in enumerate(merged):
        # Slight vertical padding preserves descenders while staying row-local.
        yy0=max(0,s-3);yy1=min(h,e+3)
        crop=im.crop((x0,yy0,x1,yy1))
        # 3x scale is readable while keeping a whole line in one image.
        crop=crop.resize((crop.width*3,crop.height*3))
        strips.append({"line_id":f"L{idx:03d}","y":[yy0,yy1],"x":[x0,x1],"image":crop})
    return strips,{"x0":x0,"x1":x1,"y0":y0,"y1":y1,"line_count":len(strips)}

def make_sheet(group):
    margin=54;gap=6
    widths=[g["image"].width for g in group]
    heights=[g["image"].height for g in group]
    W=margin+max(widths)+10
    H=sum(heights)+gap*(len(group)+1)
    sheet=Image.new("RGB",(W,H),"white")
    d=ImageDraw.Draw(sheet);font=ImageFont.load_default()
    y=gap
    for g in group:
        d.text((4,y+max(0,(g["image"].height-10)//2)),g["line_id"],fill="black",font=font)
        sheet.paste(g["image"],(margin,y))
        y+=g["image"].height+gap
    return _jpeg(sheet)

def discover_lines(client,img_bytes,target_refs,model):
    strips,segmeta=segment_lines(img_bytes)
    by_id={s["line_id"]:s for s in strips}
    discovered=defaultdict(list)
    # Contact sheets retain whole-line structure and dramatically enlarge text.
    for start in range(0,len(strips),10):
        group=strips[start:start+10]
        sheet=make_sheet(group)
        prompt=SHEET_PROMPT.format(refs=json.dumps(target_refs,ensure_ascii=False))
        for pass_i in range(2):
            try:data=_ask(client,sheet,prompt,model,5000)
            except Exception:continue
            for rec in data.get("records") or []:
                ref=str(rec.get("ref") or "")
                lid=str(rec.get("line_id") or "").upper()
                raw=str(rec.get("raw_line") or "").strip()
                wanted=next((r for r in target_refs if _norm_ref(r)==_norm_ref(ref)),None)
                if wanted and lid in by_id and _norm_ref(wanted) in _norm_ref(raw):
                    discovered[wanted].append({"line_id":lid,"raw_line":raw,"method":f"SHEET_PASS_{pass_i+1}"})

    # Verify each candidate on the exact enlarged strip, independently.
    verified={}
    evidence={}
    for ref in target_refs:
        candidates=discovered.get(ref,[])
        lid_counts=defaultdict(int)
        for c in candidates:lid_counts[c["line_id"]]+=1
        lids=sorted(lid_counts,key=lambda lid:lid_counts[lid],reverse=True)
        vreads=[]
        for lid in lids[:3]:
            strip=by_id[lid]
            img=_jpeg(strip["image"])
            for j in range(3):
                try:d=_ask(client,img,VERIFY_PROMPT.format(ref=ref),model,2200)
                except Exception:continue
                raw=str(d.get("raw_line") or "").strip()
                seen=str(d.get("ref") or "")
                if d.get("found") and (_norm_ref(ref)==_norm_ref(seen) or _norm_ref(ref) in _norm_ref(raw)):
                    if _norm_ref(ref) in _norm_ref(raw):
                        vreads.append({"line_id":lid,"raw_line":raw,"method":f"STRIP_VERIFY_{j+1}"})
            if len([x for x in vreads if x["line_id"]==lid])>=2:break

        buckets=defaultdict(list)
        for x in vreads:buckets[x["line_id"]].append(x)
        if buckets:
            best_lid,best_reads=max(buckets.items(),key=lambda kv:len(kv[1]))
            if len(best_reads)>=2:
                # choose longest agreeing transcription from same exact physical line
                best=max(best_reads,key=lambda x:len(x["raw_line"]))
                verified[ref]=best["raw_line"]
                evidence[ref]={"accepted_line_id":best_lid,"verify_votes":len(best_reads),
                               "sheet_candidates":candidates,"strip_reads":best_reads,
                               "strip_geometry":{"y":by_id[best_lid]["y"],"x":by_id[best_lid]["x"]}}
                continue
        evidence[ref]={"accepted_line_id":None,"verify_votes":0,"sheet_candidates":candidates,"strip_reads":vreads}
    return verified,evidence,segmeta

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

        truth=_truth_map();pages=frozen_v2.PAGE_IMAGES_B64
        model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
        by_page=defaultdict(list)
        for e in parent["_remaining"]:by_page[int(e["page"])].append(e)
        STATE["total_pages"]=len(by_page)

        all_predictions={};page_audit={}
        STATE["phase"]="SEGMENT_AND_TRANSCRIBE_HORIZONTAL_LINES"
        for pi,(page,errs) in enumerate(sorted(by_page.items()),1):
            STATE["current_page"]=page
            refs=list(dict.fromkeys(str(e["ref"]) for e in errs))
            img=base64.b64decode(pages[str(page)])
            verified,evidence,segmeta=discover_lines(client,img,refs,model)
            page_audit[str(page)]={"segmentation":segmeta,"evidence":evidence,"verified_lines":verified}
            for ref,raw in verified.items():all_predictions[(page,ref)]=raw
            STATE["pages_completed"]=pi

        repaired=[];correct=0
        STATE["phase"]="EXACT_FIELD_GRADING"
        for e in parent["_remaining"]:
            case_id=str(e["case_id"]);page=int(e["page"]);ref=str(e["ref"]);field=str(e["field"])
            expected=_canon(field,truth[case_id].get(field))
            raw=all_predictions.get((page,ref),"")
            got=_canon(field,parse_field(field,raw))
            passed=(got==expected)
            if passed:correct+=1
            repaired.append({"case_id":case_id,"page":page,"ref":ref,"field":field,
                             "expected":expected,"repaired":got,"passed":passed,
                             "raw_listing":raw,
                             "line_evidence":page_audit.get(str(page),{}).get("evidence",{}).get(ref,{})})

        cumulative=EXPECTED_LOCKED+correct
        acc=round(100*cumulative/EXPECTED_TOTAL,4)
        status="TRAINING_PASS" if correct==EXPECTED_REMAINING else "TRAINING_HOLD"
        result={"version":VERSION,"mode":MODE,"status":status,
                "parent":{"version":EXPECTED_PARENT_VERSION,"parent_run_id":parent["run_id"],
                          "locked_pass_checks":EXPECTED_LOCKED,"remaining_fields":EXPECTED_REMAINING,
                          "source_exam_id":EXPECTED_EXAM,"prediction_freeze_sha256":EXPECTED_FREEZE,
                          "preserved_immutable":True},
                "repair":{"repair_checks":EXPECTED_REMAINING,"repaired_correct":correct,
                          "repair_accuracy":round(100*correct/EXPECTED_REMAINING,4),
                          "remaining_failures":EXPECTED_REMAINING-correct,"repairs":repaired},
                "page_audit":page_audit,
                "cumulative_training_closure":{"correct_checks":cumulative,"total_checks":EXPECTED_TOTAL,"accuracy":acc,
                    "scientific_note":"199 checks remain locked from 6.3.1. Only 11 failed fields are addressed. Horizontal text lines are detected mechanically from image ink projection; target references are identified on enlarged whole-line contact sheets and independently verified on exact line strips. Truth is used only after transcription for grading."},
                "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3_CERTIFICATION" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_REMAINING_V635_FAILURES",
                "safety":{"fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                          "source_exam_mutations":0,"truth_mutations":0,"canonical_property_writes":0,
                          "canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0,
                          "semantic_student_mutations":0,"failed_v632_mutations":0,"failed_v633_mutations":0,
                          "failed_v634_mutations":0}}
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_line_strip_v635_runs(
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
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Line Strip Repair 6.3.5</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1400px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Line Strip Segmentation Repair 6.3.5</b><br><small>Lock 199/210 · mechanical line detection · enlarged full-line OCR · no fresh pages</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Pages {html.escape(str(s.get("pages_completed")))} / {html.escape(str(s.get("total_pages")))} · Current {html.escape(str(s.get("current_page")))}<br>
Repair accuracy {html.escape(str(r.get("repair_accuracy")))}% · Cumulative {html.escape(str(c.get("correct_checks")))} / {html.escape(str(c.get("total_checks")))} ({html.escape(str(c.get("accuracy")))}%)</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-line-strip-v635/status"):
        @app.get("/api/property-brain/magazine-line-strip-v635/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/magazine-line-strip-v635"):
        @app.get("/property-brain/magazine-line-strip-v635",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V635_DELAY","55")));run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-line-strip-v635").start()
    return STATE
