from __future__ import annotations
import base64, html, json, os, re, threading, time
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

import httpx
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION="6.6.0-ALLIANCE-MAGAZINE-SAFE-VISION-GATEWAY"
MODE="LOCK_199_QUOTA_AWARE_PROVIDER_FAILOVER_CIRCUIT_BREAKER_LOW_CALL_MULTI_TARGET_NO_FALSE_TRAINING_FAILURE"

EXPECTED_EXAM="MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_FREEZE="ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC="5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_PARENT_VERSION="6.3.1-ALLIANCE-MAGAZINE-FAILURE-ONLY-FIELD-CHALLENGER-HISTORICAL-PARENT-PIN"
EXPECTED_LOCKED=199
EXPECTED_TOTAL=210
EXPECTED_REMAINING=11

STATE={
    "status":"NOT_STARTED","result":None,"phase":"WAITING","started_at":None,"finished_at":None,
    "pages_completed":0,"total_pages":2,"current_page":None,"last_error":None,
    "next_retry_at":None,"provider_state":{}
}
_LOCK=threading.Lock();_STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_safe_gateway_v660_runs(
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

PROMPT="""You are a forensic vision extractor reading ONE complete real-estate magazine page.

TARGET REFERENCES:
{refs}

Return JSON exactly:
{{"records":[{{"ref":"","raw_line":""}}]}}

Rules:
1. Return only the target property rows that are visibly present on this page.
2. One target reference = one complete property row.
3. Preserve all digits exactly: area, floor, BHK/BR, @price and listing-owned phone numbers.
4. Do not use page header, footer, broker office address, advertisements or adjacent property rows.
5. If a target is not confidently readable, omit it.
6. Do not infer or repair digits.
"""

def _engine(c): return getattr(c,"engine",None)
def _app(c): return getattr(c,"app",None) or c
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False

def _json_text(s):
    s=(s or "").strip()
    s=re.sub(r"^```(?:json)?\s*","",s); s=re.sub(r"\s*```$","",s)
    return json.loads(s)

def _norm_ref(x): return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())

def _clean_records(data,provider_label):
    out=[]
    for rec in (data.get("records") or []):
        ref=str(rec.get("ref") or "").strip()
        raw=str(rec.get("raw_line") or "").strip()
        if not ref or not raw: continue
        out.append({"ref":ref,"raw_line":raw,"provider":provider_label})
    return out

def _is_quota(exc):
    s=str(exc)
    return ("429" in s and ("RESOURCE_EXHAUSTED" in s or "quota" in s.lower())) or "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in s

def _is_daily_quota(exc):
    s=str(exc)
    return "PerDay" in s or "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in s

def _next_day_utc():
    now=datetime.now(timezone.utc)
    nxt=(now+timedelta(days=1)).replace(hour=0,minute=5,second=0,microsecond=0)
    return nxt

class ProviderGateway:
    def __init__(self):
        self.providers=[]
        self.cooldown={}
        self.events=[]
        self.calls=0
        self.max_calls=int(os.getenv("ALLIANCE_MAGAZINE_V660_MAX_CALLS","8"))
        self._build()

    def _build(self):
        # Gemini keys. Extra keys are optional and automatic if present.
        keys=[]
        for name in ["GEMINI_API_KEY","GEMINI_API_KEY_2","GEMINI_API_KEY_3","GEMINI_API_KEY_4"]:
            val=(os.getenv(name) or "").strip()
            if val and val not in keys: keys.append(val)

        models=[]
        primary=(os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite").strip()
        models.append(primary)
        extra=(os.getenv("GEMINI_FALLBACK_MODELS") or "").strip()
        for m in [x.strip() for x in extra.split(",") if x.strip()]:
            if m not in models:models.append(m)

        for ki,key in enumerate(keys,1):
            client=genai.Client(api_key=key)
            for model in models:
                self.providers.append({
                    "kind":"gemini","label":f"GEMINI_KEY_{ki}:{model}",
                    "client":client,"model":model
                })

        # Optional OpenRouter multimodal fallback. No dependency change: httpx already exists.
        ork=(os.getenv("OPENROUTER_API_KEY") or "").strip()
        orm=(os.getenv("OPENROUTER_VISION_MODEL") or "").strip()
        if ork and orm:
            self.providers.append({
                "kind":"openrouter","label":f"OPENROUTER:{orm}",
                "api_key":ork,"model":orm
            })

    def _available(self,p):
        until=self.cooldown.get(p["label"])
        return not until or datetime.now(timezone.utc)>=until

    def _mark_quota(self,p,exc):
        if _is_daily_quota(exc):
            until=_next_day_utc()
        else:
            until=datetime.now(timezone.utc)+timedelta(minutes=2)
        self.cooldown[p["label"]]=until
        self.events.append({"provider":p["label"],"event":"QUOTA_COOLDOWN","until":until.isoformat(),"error":str(exc)[:1200]})

    def _call_gemini(self,p,img,prompt):
        resp=p["client"].models.generate_content(
            model=p["model"],
            contents=[prompt,types.Part.from_bytes(data=img,mime_type="image/jpeg")],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",temperature=0.0,max_output_tokens=7000
            )
        )
        return _json_text(resp.text or "")

    def _call_openrouter(self,p,img,prompt):
        b64=base64.b64encode(img).decode("ascii")
        payload={
            "model":p["model"],
            "temperature":0,
            "messages":[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
            ]}]
        }
        with httpx.Client(timeout=90.0) as h:
            r=h.post("https://openrouter.ai/api/v1/chat/completions",
                     headers={"Authorization":"Bearer "+p["api_key"],"Content-Type":"application/json"},
                     json=payload)
            if r.status_code==429:
                raise RuntimeError("429 OPENROUTER_QUOTA "+r.text[:1000])
            r.raise_for_status()
            content=r.json()["choices"][0]["message"]["content"]
            return _json_text(content)

    def ask(self,img,prompt):
        if self.calls>=self.max_calls:
            return None,{"status":"REQUEST_BUDGET_EXHAUSTED","events":self.events[-20:]}
        attempted=0
        for p in self.providers:
            if not self._available(p):continue
            if self.calls>=self.max_calls:break
            attempted+=1;self.calls+=1
            try:
                data=self._call_gemini(p,img,prompt) if p["kind"]=="gemini" else self._call_openrouter(p,img,prompt)
                self.events.append({"provider":p["label"],"event":"SUCCESS"})
                return data,{"status":"OK","provider":p["label"]}
            except Exception as exc:
                if _is_quota(exc):
                    self._mark_quota(p,exc);continue
                self.events.append({"provider":p["label"],"event":"ERROR","error":f"{type(exc).__name__}: {exc}"[:1200]})
                continue
        if not self.providers:
            return None,{"status":"NO_CONFIGURED_VISION_PROVIDER","events":self.events[-20:]}
        if attempted==0:
            return None,{"status":"ALL_PROVIDERS_COOLDOWN","events":self.events[-20:]}
        return None,{"status":"ALL_PROVIDERS_FAILED","events":self.events[-20:]}

    def next_retry(self):
        futures=[x for x in self.cooldown.values() if x>datetime.now(timezone.utc)]
        return min(futures) if futures else None

def _record_match(ref,rec):
    nr=_norm_ref(ref); rr=_norm_ref(rec.get("ref"))
    raw=_norm_ref(str(rec.get("raw_line") or "")[:50])
    return rr==nr or (nr and raw.startswith(nr))

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

def _field_consensus(field,cands):
    vals=[]
    for c in cands:
        v=_canon(field,parse_field(field,c["raw_line"]))
        if v not in ("",[]): vals.append((json.dumps(v,sort_keys=True),v,c))
    if not vals:return None,{"votes":0,"candidates":[]}
    counts=Counter(k for k,_,_ in vals)
    best_key,best_votes=counts.most_common(1)[0]
    best_v=next(v for k,v,_ in vals if k==best_key)
    evidence=[{"provider":c["provider"],"value":v,"raw_line":c["raw_line"]} for k,v,c in vals if k==best_key]
    return (best_v if best_votes>=2 else None),{"votes":best_votes,"total":len(vals),"evidence":evidence}

def _load_parent(engine):
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT run_id,version,parent_version,source_exam_id,source_prediction_freeze_sha256,
                 locked_pass_checks,repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result,created_at
          FROM alliance_magazine_failure_only_v630_runs
          WHERE version=:v AND source_exam_id=:e AND source_prediction_freeze_sha256=:p
            AND cumulative_correct=:cc AND repaired_correct=5 AND repair_checks=16
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
    return {
        "version":VERSION,"mode":MODE,"status":STATE["status"],"phase":STATE["phase"],
        "started_at":STATE["started_at"],"finished_at":STATE["finished_at"],
        "pages_completed":STATE["pages_completed"],"total_pages":STATE["total_pages"],
        "current_page":STATE["current_page"],"last_error":STATE["last_error"],
        "next_retry_at":STATE["next_retry_at"],"provider_state":STATE["provider_state"],
        "result_ready":bool(STATE.get("result"))
    }

def run_once(core):
    if not _LOCK.acquire(False):return _state()
    try:
        STATE.update(status="RUNNING",phase="PIN_631_PARENT",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,pages_completed=0,current_page=None,last_error=None,next_retry_at=None)
        engine=_engine(core)
        if engine is None:raise RuntimeError("Core engine unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:raise RuntimeError("Semantic student changed")
        with engine.begin() as c:c.execute(text(DDL))
        parent=_load_parent(engine)
        if not parent:raise RuntimeError("Exact 6.3.1 parent at 199/210 not found")

        gw=ProviderGateway()
        STATE["provider_state"]={"configured":[p["label"] for p in gw.providers],"max_calls":gw.max_calls}

        truth=_truth_map();pages=frozen_v2.PAGE_IMAGES_B64
        by_page=defaultdict(list)
        for e in parent["_remaining"]:by_page[int(e["page"])].append(e)
        STATE["total_pages"]=len(by_page)

        candidates_by_case=defaultdict(list);page_audit={}
        STATE["phase"]="LOW_CALL_MULTI_TARGET_EXTRACTION"

        for pi,(page,errs) in enumerate(sorted(by_page.items()),1):
            STATE["current_page"]=page
            refs=list(dict.fromkeys(str(e["ref"]) for e in errs))
            img=base64.b64decode(pages[str(page)])
            prompt=PROMPT.format(refs=json.dumps(refs,ensure_ascii=False))

            calls=[]
            # Two independent page reads are enough for consensus; gateway handles failover.
            for pass_i in range(2):
                data,meta=gw.ask(img,prompt)
                calls.append(meta)
                if data:
                    recs=_clean_records(data,meta.get("provider","UNKNOWN"))
                    for e in errs:
                        case_id=str(e["case_id"]);ref=str(e["ref"])
                        for rec in recs:
                            if _record_match(ref,rec):
                                candidates_by_case[case_id].append(rec)
                else:
                    # Stop immediately on quota/cooldown. Do not burn the remaining call budget.
                    if meta.get("status") in {"ALL_PROVIDERS_COOLDOWN","ALL_PROVIDERS_FAILED","NO_CONFIGURED_VISION_PROVIDER","REQUEST_BUDGET_EXHAUSTED"}:
                        break

            page_audit[str(page)]={"refs":refs,"calls":calls,
                                   "candidate_counts":{str(e["case_id"]):len(candidates_by_case[str(e["case_id"])]) for e in errs}}
            STATE["pages_completed"]=pi

            # If provider infrastructure is unavailable, pause safely. Never grade blanks as training failures.
            if not any(len(candidates_by_case[str(e["case_id"])]) for e in errs):
                last=calls[-1] if calls else {}
                if last.get("status")!="OK":
                    retry=gw.next_retry()
                    STATE["next_retry_at"]=retry.isoformat() if retry else None
                    result={
                        "version":VERSION,"mode":MODE,"status":"WAITING_FOR_PROVIDER_QUOTA",
                        "parent":{"version":EXPECTED_PARENT_VERSION,"locked_pass_checks":EXPECTED_LOCKED,
                                  "source_exam_id":EXPECTED_EXAM,"prediction_freeze_sha256":EXPECTED_FREEZE,
                                  "preserved_immutable":True},
                        "fault":{"root_cause":"VISION_PROVIDER_QUOTA_OR_AVAILABILITY",
                                 "training_result_not_changed":True,
                                 "provider_events":gw.events[-50:],
                                 "configured_providers":[p["label"] for p in gw.providers],
                                 "calls_used":gw.calls,"max_calls":gw.max_calls,
                                 "next_retry_at":STATE["next_retry_at"]},
                        "page_audit":page_audit,
                        "cumulative_training_closure":{"correct_checks":EXPECTED_LOCKED,"total_checks":EXPECTED_TOTAL,
                                                       "accuracy":round(100*EXPECTED_LOCKED/EXPECTED_TOTAL,4),
                                                       "scientific_note":"Provider quota/availability blocked inference. Blank outputs are NOT scored as student failures."},
                        "next_gate":"AUTO_RETRY_WHEN_PROVIDER_AVAILABLE",
                        "safety":{"fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                                  "source_exam_mutations":0,"truth_mutations":0,"canonical_property_writes":0,
                                  "canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0}
                    }
                    with engine.begin() as c:
                        c.execute(text("""INSERT INTO alliance_magazine_safe_gateway_v660_runs(
                          version,parent_version,source_exam_id,source_prediction_freeze_sha256,locked_pass_checks,
                          repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result)
                          VALUES(:v,:pv,:e,:p,:l,:rc,:rco,:cc,:a,:s,CAST(:r AS JSONB))"""),
                          {"v":VERSION,"pv":EXPECTED_PARENT_VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,
                           "l":EXPECTED_LOCKED,"rc":EXPECTED_REMAINING,"rco":0,"cc":EXPECTED_LOCKED,
                           "a":round(100*EXPECTED_LOCKED/EXPECTED_TOTAL,4),"s":"WAITING_FOR_PROVIDER_QUOTA",
                           "r":json.dumps(result,ensure_ascii=False)})
                    STATE.update(status="WAITING_FOR_PROVIDER_QUOTA",result=result,phase="PAUSED_SAFE",
                                 finished_at=datetime.now(timezone.utc).isoformat(),current_page=None)
                    return result

        repaired=[];correct=0
        STATE["phase"]="CONSENSUS_AND_EXACT_GRADING"
        for e in parent["_remaining"]:
            case_id=str(e["case_id"]);page=int(e["page"]);ref=str(e["ref"]);field=str(e["field"])
            expected=_canon(field,truth[case_id].get(field))
            predicted,meta=_field_consensus(field,candidates_by_case[case_id])
            got=_canon(field,predicted)
            passed=(predicted is not None and got==expected)
            if passed:correct+=1
            repaired.append({"case_id":case_id,"page":page,"ref":ref,"field":field,
                             "expected":expected,"repaired":got,"passed":passed,
                             "candidate_count":len(candidates_by_case[case_id]),"consensus":meta})

        cumulative=EXPECTED_LOCKED+correct
        acc=round(100*cumulative/EXPECTED_TOTAL,4)
        status="TRAINING_PASS" if correct==EXPECTED_REMAINING else "TRAINING_HOLD"
        result={
            "version":VERSION,"mode":MODE,"status":status,
            "parent":{"version":EXPECTED_PARENT_VERSION,"locked_pass_checks":EXPECTED_LOCKED,
                      "source_exam_id":EXPECTED_EXAM,"prediction_freeze_sha256":EXPECTED_FREEZE,
                      "preserved_immutable":True},
            "fault_fixed":{"quota_circuit_breaker":True,"provider_failover":True,"request_budget":gw.max_calls,
                           "low_call_design":True,"false_blank_grading_blocked":True,
                           "provider_events":gw.events[-50:]},
            "repair":{"repair_checks":EXPECTED_REMAINING,"repaired_correct":correct,
                      "repair_accuracy":round(100*correct/EXPECTED_REMAINING,4),
                      "remaining_failures":EXPECTED_REMAINING-correct,"repairs":repaired},
            "page_audit":page_audit,
            "cumulative_training_closure":{"correct_checks":cumulative,"total_checks":EXPECTED_TOTAL,"accuracy":acc,
                "scientific_note":"199 passed checks remain locked. Only successful provider outputs are graded. Provider quota failures never become student failures."},
            "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3_CERTIFICATION" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_GENUINE_V660_FAILURES",
            "safety":{"fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                      "source_exam_mutations":0,"truth_mutations":0,"canonical_property_writes":0,
                      "canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0}
        }
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_safe_gateway_v660_runs(
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
    s=status(core);r=s.get("repair") or {};c=s.get("cumulative_training_closure") or {};f=s.get("fault") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='15'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Safe Vision Gateway 6.6</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1500px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Safe Vision Gateway 6.6</b><br>
<small>Quota-aware · provider failover · circuit breaker · low-call extraction · no false training failures</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Phase {html.escape(str(s.get("phase")))} · Pages {html.escape(str(s.get("pages_completed")))} / {html.escape(str(s.get("total_pages")))}<br>
Next retry {html.escape(str(s.get("next_retry_at")))}<br>
Repair {html.escape(str(r.get("repaired_correct")))} / {html.escape(str(r.get("repair_checks")))} · Cumulative {html.escape(str(c.get("correct_checks")))} / {html.escape(str(c.get("total_checks")))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-safe-v660/status"):
        @app.get("/api/property-brain/magazine-safe-v660/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/magazine-safe-v660"):
        @app.get("/property-brain/magazine-safe-v660",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    # Initial run, then automatic hourly retry while quota blocked.
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V660_DELAY","45")))
    while True:
        result=run_once(core)
        if isinstance(result,dict) and result.get("status")=="WAITING_FOR_PROVIDER_QUOTA":
            time.sleep(3600)
            continue
        break

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-safe-v660").start()
    return STATE
