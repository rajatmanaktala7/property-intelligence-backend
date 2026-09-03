from __future__ import annotations
import base64, html, io, json, os, re, threading, time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from google.genai import types
from PIL import Image
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION="6.3.3-ALLIANCE-MAGAZINE-DETERMINISTIC-TILE-LOCATOR-REPAIR"
MODE="LOCK_199_OF_210_FROM_631_REPAIR_ONLY_11_FIELDS_DETERMINISTIC_GRID_SCAN_NO_MODEL_BBOX_NO_FRESH_EXAM"

EXPECTED_EXAM="MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_FREEZE="ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC="5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_PARENT_VERSION="6.3.1-ALLIANCE-MAGAZINE-FAILURE-ONLY-FIELD-CHALLENGER-HISTORICAL-PARENT-PIN"
EXPECTED_LOCKED=199
EXPECTED_TOTAL=210
EXPECTED_REMAINING=11

STATE={"status":"NOT_STARTED","result":None,"phase":"WAITING","started_at":None,"finished_at":None,
       "repairs_completed":0,"total_repairs":EXPECTED_REMAINING,"current_repair":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_tile_locator_v633_runs(
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

REF_PROMPT="""You are looking at ONE deterministic crop from a dense real-estate magazine page.

REQUESTED PROPERTY REFERENCE: {ref}

Return JSON exactly:
{{"found":true|false,"reference_seen":"","evidence_text":""}}

found=true only if the exact requested property reference is visibly printed in this crop as an inventory listing reference.
Do not match office addresses, broker addresses, phone numbers, headers, footers, or similar neighboring references.
evidence_text must include the visible requested reference and nearby listing text.
"""

FIELD_PROMPT="""You are looking at a deterministic crop that contains the requested real-estate listing.

REQUESTED PROPERTY REFERENCE: {ref}
FIELD TO READ: {field}

Return JSON exactly:
{{"found":true|false,"reference_seen":"","value":null,"evidence_text":""}}

Read only the exact listing beginning at the requested reference.
Do not borrow anything from another property, broker footer, office footer, advertisement, or neighboring row.

Field rules:
- phones: return all phone numbers belonging to this exact listing as a JSON list. Preserve digits exactly. Expand compact slash shorthand.
- floor: return only BMT/GF/FF/SF/TF/TERR tokens for this listing, joined by + in printed order.
- bedrooms: return number only, e.g. "2", "3", "4+1".
- price: return only the explicit @ price for this listing, e.g. "5CR", "1.65CR", "40L".
If the exact field is not confidently visible in this crop, found=false.
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

def _ask(client,img,prompt,model,tokens=1400):
    return _json(client.models.generate_content(
        model=model,
        contents=[prompt,types.Part.from_bytes(data=img,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(response_mime_type="application/json",temperature=0.0,max_output_tokens=tokens)
    ))

def _jpeg(im):
    b=io.BytesIO();im.save(b,format="JPEG",quality=99);return b.getvalue()

def _tile_grid(img_bytes,rows=14,cols=4,scale=4):
    im=Image.open(io.BytesIO(img_bytes)).convert("RGB");w,h=im.size
    rh=h/rows;cw=w/cols
    oy=max(20,int(h*.012));ox=max(16,int(w*.012))
    out=[]
    for r in range(rows):
        for c in range(cols):
            y0=max(0,int(r*rh)-oy);y1=min(h,int((r+1)*rh)+oy)
            x0=max(0,int(c*cw)-ox);x1=min(w,int((c+1)*cw)+ox)
            crop=im.crop((x0,y0,x1,y1))
            crop=crop.resize((crop.width*scale,crop.height*scale))
            out.append({"row":r,"col":c,"box":[x0,y0,x1,y1],"img":_jpeg(crop)})
    return out,(w,h)

def _stripe(img_bytes,row_idx,rows=14,scale=4):
    im=Image.open(io.BytesIO(img_bytes)).convert("RGB");w,h=im.size
    rh=h/rows;oy=max(28,int(h*.018))
    y0=max(0,int(row_idx*rh)-oy);y1=min(h,int((row_idx+1)*rh)+oy)
    crop=im.crop((0,y0,w,y1));crop=crop.resize((crop.width*scale,crop.height*scale))
    return _jpeg(crop),[0,y0,w,y1]

def _norm_phones(v):
    vals=v if isinstance(v,list) else [v] if v not in (None,"") else []
    out=[]
    for x in vals:
        s=re.sub(r"[\s-]","",str(x or ""))
        m=re.fullmatch(r"([6-9]\d{9})/(\d{1,4})",s)
        if m:
            b,suf=m.groups();out.extend([b,b[:-len(suf)]+suf]);continue
        if re.fullmatch(r"[6-9]\d{9}",s):out.append(s);continue
        if re.fullmatch(r"0\d{10}",s):out.append(s);continue
    return sorted(dict.fromkeys(out))

def _canon(field,v):
    if field=="phones":return _norm_phones(v)
    s=str(v or "").strip().upper().replace(" ","")
    if field=="bedrooms":
        m=re.search(r"(\d+(?:\+\d+)?)",s);return m.group(1) if m else ""
    if field=="floor":
        toks=re.findall(r"BMT|GF|FF|SF|TF|TERR",s);out=[]
        for t in toks:
            if t not in out:out.append(t)
        return "+".join(out)
    if field=="price":
        return s.replace("CRORES","CR").replace("CRORE","CR").replace("LAKHS","L").replace("LAKH","L").replace("LAC","L")
    return s

def _ref_hit(client,tile,ref,model):
    try:d=_ask(client,tile["img"],REF_PROMPT.format(ref=ref),model,1000)
    except Exception:return None
    if not d.get("found"):return None
    seen=str(d.get("reference_seen") or "");ev=str(d.get("evidence_text") or "")
    nr=_norm_ref(ref)
    if nr not in _norm_ref(seen) and nr not in _norm_ref(ev):return None
    return {"row":tile["row"],"col":tile["col"],"box":tile["box"],"evidence":ev}

def _read_field(client,img,ref,field,model,label):
    try:d=_ask(client,img,FIELD_PROMPT.format(ref=ref,field=field),model,1200)
    except Exception:return None
    if not d.get("found"):return None
    seen=str(d.get("reference_seen") or "");ev=str(d.get("evidence_text") or "")
    nr=_norm_ref(ref)
    if nr not in _norm_ref(seen) and nr not in _norm_ref(ev):return None
    val=_canon(field,d.get("value"))
    if val in ("",[]):return None
    return {"value":val,"label":label,"evidence":ev}

def _consensus(cands):
    if not cands:return None,{"reason":"NO_FIELD_CANDIDATES"}
    buckets=defaultdict(list)
    for c in cands:buckets[json.dumps(c["value"],sort_keys=True)].append(c)
    ranked=sorted(buckets.items(),key=lambda kv:(len(kv[1]),len(kv[0])),reverse=True)
    k,items=ranked[0]
    return json.loads(k),{"votes":len(items),"total_candidates":len(cands),
                          "methods":[x["label"] for x in items],
                          "evidence":[x["evidence"] for x in items[:5]]}

def repair_one(client,img_bytes,ref,field,model):
    tiles,_=_tile_grid(img_bytes,14,4,4)
    hits=[]
    # deterministic exhaustive reference scan; no learned bbox required
    for t in tiles:
        h=_ref_hit(client,t,ref,model)
        if h:hits.append(h)

    if not hits:
        # coarser fallback grid, still deterministic
        tiles2,_=_tile_grid(img_bytes,10,3,4)
        for t in tiles2:
            h=_ref_hit(client,t,ref,model)
            if h:hits.append(h)

    if not hits:
        return None,{"locator":{"reason":"REFERENCE_NOT_FOUND_IN_DETERMINISTIC_GRID"},"field":{"reason":"NO_REFERENCE_TILE"}}

    # group hits by horizontal row; exact listing should cluster in same band
    row_counts=defaultdict(int)
    for h in hits:row_counts[h["row"]]+=1
    best_hit=max(hits,key=lambda h:(row_counts[h["row"]],-h["col"]))
    target_row=best_hit["row"]

    cands=[]
    hit_meta=[]
    # Read from every exact-hit tile
    for i,h in enumerate(hits,1):
        # find original tile image by matching box
        match=next((t for t in tiles if t["box"]==h["box"]),None)
        if match:
            c=_read_field(client,match["img"],ref,field,model,f"HIT_TILE_{i}")
            if c:cands.append(c)
        hit_meta.append(h)

    # Always read a full-width horizontal stripe through the winning row.
    stripe,box=_stripe(img_bytes,target_row,14,4)
    for j in range(3):
        c=_read_field(client,stripe,ref,field,model,f"FULL_WIDTH_STRIPE_{j+1}")
        if c:cands.append(c)

    value,meta=_consensus(cands)
    if value is not None and meta.get("votes",0)>=2:
        meta["path"]="DETERMINISTIC_TILE_PLUS_STRIPE_CONSENSUS"
    elif value is not None:
        meta["path"]="DETERMINISTIC_BEST_AVAILABLE"
    return value,{"locator":{"hit_count":len(hits),"hits":hit_meta,"target_row":target_row,"stripe_box":box},"field":meta}

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
            "repairs_completed":STATE["repairs_completed"],"total_repairs":STATE["total_repairs"],
            "current_repair":STATE["current_repair"],"last_error":STATE["last_error"],
            "result_ready":bool(STATE.get("result"))}

def run_once(core):
    if not _LOCK.acquire(False):return _state()
    try:
        STATE.update(status="RUNNING",phase="PIN_631_PARENT",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,repairs_completed=0,current_repair=None,last_error=None)
        engine=_engine(core);client=_client(core)
        if engine is None or client is None:raise RuntimeError("Core engine/Gemini client unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:raise RuntimeError("Semantic student changed")
        with engine.begin() as c:c.execute(text(DDL))
        parent=_load_parent(engine)
        if not parent:raise RuntimeError("Exact 6.3.1 parent with 199/210 and 11 remaining failures not found")

        truth=_truth_map();pages=frozen_v2.PAGE_IMAGES_B64;model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
        results=[];correct=0
        STATE["phase"]="DETERMINISTIC_TILE_SCAN_REPAIR"
        for i,e in enumerate(parent["_remaining"],1):
            case_id=str(e["case_id"]);page=int(e["page"]);ref=str(e["ref"]);field=str(e["field"])
            STATE["current_repair"]=f"{case_id}:{field}"
            expected=_canon(field,truth[case_id].get(field))
            img=base64.b64decode(pages[str(page)])
            got,meta=repair_one(client,img,ref,field,model)
            got=_canon(field,got);passed=(got==expected)
            if passed:correct+=1
            results.append({"case_id":case_id,"page":page,"ref":ref,"field":field,
                            "expected":expected,"repaired":got,"passed":passed,"evidence":meta})
            STATE["repairs_completed"]=i

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
                          "remaining_failures":EXPECTED_REMAINING-correct,"repairs":results},
                "cumulative_training_closure":{"correct_checks":cumulative,"total_checks":EXPECTED_TOTAL,"accuracy":acc,
                    "scientific_note":"199 checks remain locked from 6.3.1. Only 11 failed fields are scanned with deterministic image tiles and horizontal stripes. Model-generated bbox coordinates are not used. This is training, not fresh certification."},
                "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3_CERTIFICATION" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_REMAINING_V633_FAILURES",
                "safety":{"fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                          "source_exam_mutations":0,"truth_mutations":0,"canonical_property_writes":0,
                          "canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0,
                          "semantic_student_mutations":0,"failed_v632_mutations":0}}
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_tile_locator_v633_runs(
              version,parent_version,source_exam_id,source_prediction_freeze_sha256,locked_pass_checks,
              repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result)
              VALUES(:v,:pv,:e,:p,:l,:rc,:rco,:cc,:a,:s,CAST(:r AS JSONB))"""),
              {"v":VERSION,"pv":EXPECTED_PARENT_VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,
               "l":EXPECTED_LOCKED,"rc":EXPECTED_REMAINING,"rco":correct,"cc":cumulative,
               "a":acc,"s":status,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status=status,result=result,phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),current_repair=None)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",last_error=f"{type(exc).__name__}: {exc}",
                     finished_at=datetime.now(timezone.utc).isoformat(),current_repair=None)
        return _state()
    finally:_LOCK.release()

def status(core):
    if STATE.get("result"):return STATE["result"]
    return _state()

def dashboard(core):
    s=status(core);r=s.get("repair") or {};c=s.get("cumulative_training_closure") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Deterministic Tile Locator 6.3.3</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1400px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Deterministic Tile Locator Repair 6.3.3</b><br><small>Lock 199/210 · deterministic grid scan · no model bbox · no fresh pages</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Repair progress {html.escape(str(s.get("repairs_completed")))} / {html.escape(str(s.get("total_repairs")))} · Current {html.escape(str(s.get("current_repair")))}<br>
Repair accuracy {html.escape(str(r.get("repair_accuracy")))}% · Cumulative {html.escape(str(c.get("correct_checks")))} / {html.escape(str(c.get("total_checks")))} ({html.escape(str(c.get("accuracy")))}%)</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-tile-locator-v633/status"):
        @app.get("/api/property-brain/magazine-tile-locator-v633/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/magazine-tile-locator-v633"):
        @app.get("/property-brain/magazine-tile-locator-v633",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V633_DELAY","55")));run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-tile-locator-v633").start()
    return STATE
