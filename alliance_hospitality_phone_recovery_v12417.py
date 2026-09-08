from __future__ import annotations
import json, re
from urllib.parse import urlparse
from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="12.4.18A-PRESERVE-EXISTING-PHONES"

BAD_NUMBERS={"9876543210","9999999999","8888888888","1234567890","0000000000"}
WEAK_HOSTS=("justdial.","tripadvisor.","zomato.","swiggy.","magicpin.","sloshout.","wedmegood.",
            "wanderlog.","agoda.","so.city","threebestrated.","facebook.","instagram.")
GENERIC_TOKENS={"delhi","india","ncr","new","hotel","cafe","restaurant","bar","club","lounge","banquet",
                "guest","house","sector","road","the","and","resort","faridabad","noida","gurugram","gurgaon",
                "ghaziabad","greater"}

def _app(core): return getattr(core,"app",None) or core
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip())
def _host(url):
    try: return urlparse(_norm(url)).netloc.lower().replace("www.","")
    except Exception: return ""
def _phone(v):
    d=re.sub(r"\D","",str(v or ""))
    if len(d)>=10:
        d=d[-10:]
    if len(d)!=10 or d[0] not in "6789" or d in BAD_NUMBERS or len(set(d))<5:
        return None
    return d
def _tokens(v):
    return {x for x in re.findall(r"[a-z0-9]+",_norm(v).lower()) if len(x)>=3 and x not in GENERIC_TOKENS}
def _name_strength(name,textv):
    a=_tokens(name)
    if not a: return 0.0
    b=_tokens(textv)
    return len(a & b)/max(1,len(a))
def _location_terms(location):
    s=_norm(location).lower()
    terms=[]
    for x in ("connaught place","rajouri garden","janakpuri","greater kailash","saket","vasant kunj",
              "punjabi bagh","rohini","dwarka","faridabad","noida","greater noida","gurugram","gurgaon",
              "ghaziabad","delhi"):
        if x in s: terms.append(x)
    return terms
def _location_ok(location,textv):
    terms=_location_terms(location)
    if not terms: return True
    t=_norm(textv).lower()
    return any(x in t for x in terms)
def _weak_host(host): return any(x in host for x in WEAK_HOSTS)

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_phone_candidate_v12418(
          candidate_id BIGSERIAL PRIMARY KEY,
          hospitality_id BIGINT NOT NULL REFERENCES ai_hospitality_entity(hospitality_id),
          phone TEXT NOT NULL,
          confidence INT NOT NULL DEFAULT 0,
          decision TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION',
          evidence_count INT NOT NULL DEFAULT 0,
          domain_count INT NOT NULL DEFAULT 0,
          strong_domain_count INT NOT NULL DEFAULT 0,
          evidence_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
          source_summary TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(hospitality_id,phone)
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_phone_recovery_state_v12418(
          hospitality_id BIGINT PRIMARY KEY REFERENCES ai_hospitality_entity(hospitality_id),
          attempts INT NOT NULL DEFAULT 0,
          last_status TEXT NOT NULL DEFAULT 'PENDING',
          last_error TEXT,
          last_run_at TIMESTAMPTZ,
          completed BOOLEAN NOT NULL DEFAULT FALSE,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_hosp_phone_candidate_decision_v12418 ON ai_hospitality_phone_candidate_v12418(decision)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_hosp_phone_state_completed_v12418 ON ai_hospitality_phone_recovery_state_v12418(completed)"))
    return True

def quarantine_weak_existing(engine):
    # 12.4.18A safety rule: do not mass-clear existing unverified phones.
    # Current live phone rows come from mixed sources and cannot be safely
    # separated into only the weak 12.4.16 batch.
    ensure_schema(engine)
    return 0

def _item_text(item):
    return " ".join(_norm(item.get(k)) for k in ("name","title","summary","snippet","content","description","url"))

def _collect_candidates(name,location,results):
    phones={}
    for item in results:
        url=_norm(item.get("url")); host=_host(url); blob=_item_text(item)
        strength=_name_strength(name,blob)
        if strength < 0.34:
            continue
        loc_ok=_location_ok(location,blob)
        if not loc_ok and strength < 0.67:
            continue
        found=set()
        # Compact 10-digit numbers and common +91 / spaced / hyphenated forms.
        for raw in re.findall(r"(?<!\d)(?:\+?91[\s().-]*)?[6-9](?:[\s().-]*\d){9}(?!\d)",blob):
            p=_phone(raw)
            if p: found.add(p)
        for raw in re.findall(r"(?<!\d)[6-9]\d{9}(?!\d)",blob):
            p=_phone(raw)
            if p: found.add(p)
        for p in found:
            d=phones.setdefault(p,{"urls":[],"domains":set(),"strong_domains":set(),"evidence":0,"best_name_strength":0.0})
            d["evidence"]+=1
            d["best_name_strength"]=max(d["best_name_strength"],strength)
            if url and url not in d["urls"]: d["urls"].append(url)
            if host:
                d["domains"].add(host)
                if not _weak_host(host): d["strong_domains"].add(host)
    return phones

def _score_candidate(d):
    domains=len(d["domains"]); strong=len(d["strong_domains"]); ev=d["evidence"]; ns=d["best_name_strength"]
    score=20
    if ns>=0.67: score+=20
    elif ns>=0.5: score+=12
    else: score+=5
    if ev>=2: score+=15
    if domains>=2: score+=20
    if strong>=1: score+=15
    if strong>=2: score+=10
    score=min(100,score)
    # Auto-save only corroborated evidence. One-source candidates stay review-only.
    if score>=80 and domains>=2 and strong>=1:
        decision="HIGH_CONFIDENCE"
    elif score>=45:
        decision="NEEDS_VERIFICATION"
    else:
        decision="LOW_CONFIDENCE"
    return score,decision

def recover_one(engine,row):
    import alliance_v31_hospitality as h
    hid=int(row["hospitality_id"])
    name=_norm(row["business_name"])
    location=_norm(row.get("location") or row.get("city") or "Delhi NCR")
    queries=[
      f'{name} {location} phone',
      f'{name} {location} contact number',
      f'{name} {location} mobile whatsapp',
    ]
    merged=[]; errors=[]
    for q in queries:
        r=h._langsearch(q,8)
        if r.get("status")=="OK":
            merged.extend(r.get("results") or [])
        else:
            errors.append(_norm(r.get("message") or r.get("status")))
    candidates=_collect_candidates(name,location,merged)
    best=None
    with engine.begin() as c:
        for p,d in candidates.items():
            score,decision=_score_candidate(d)
            c.execute(text("""
              INSERT INTO ai_hospitality_phone_candidate_v12418(
                hospitality_id,phone,confidence,decision,evidence_count,domain_count,strong_domain_count,
                evidence_urls,source_summary,updated_at
              ) VALUES(:id,:p,:score,:decision,:ev,:domains,:strong,CAST(:urls AS jsonb),:summary,NOW())
              ON CONFLICT(hospitality_id,phone) DO UPDATE SET
                confidence=GREATEST(ai_hospitality_phone_candidate_v12418.confidence,EXCLUDED.confidence),
                decision=CASE
                  WHEN EXCLUDED.confidence>ai_hospitality_phone_candidate_v12418.confidence THEN EXCLUDED.decision
                  ELSE ai_hospitality_phone_candidate_v12418.decision END,
                evidence_count=GREATEST(ai_hospitality_phone_candidate_v12418.evidence_count,EXCLUDED.evidence_count),
                domain_count=GREATEST(ai_hospitality_phone_candidate_v12418.domain_count,EXCLUDED.domain_count),
                strong_domain_count=GREATEST(ai_hospitality_phone_candidate_v12418.strong_domain_count,EXCLUDED.strong_domain_count),
                evidence_urls=EXCLUDED.evidence_urls,source_summary=EXCLUDED.source_summary,updated_at=NOW()
            """),{"id":hid,"p":p,"score":score,"decision":decision,"ev":d["evidence"],
                  "domains":len(d["domains"]),"strong":len(d["strong_domains"]),"urls":json.dumps(d["urls"][:12]),
                  "summary":f"{d['evidence']} appearances, {len(d['domains'])} domains, {len(d['strong_domains'])} strong domains"})
            if best is None or score>best[1]:
                best=(p,score,decision,d)
        if best and best[2]=="HIGH_CONFIDENCE":
            c.execute(text("""
              UPDATE ai_hospitality_entity SET contact_phone=:p,updated_at=NOW()
              WHERE hospitality_id=:id AND COALESCE(contact_phone,'')=''
            """),{"p":best[0],"id":hid})
            c.execute(text("""
              INSERT INTO ai_hospitality_source_history(
                hospitality_id,source_type,source_name,source_url,evidence_text,raw_payload,seen_at
              ) VALUES(:id,'STRICT_PHONE_RECOVERY','LANGSEARCH_CROSSCHECK',:source_url,:ev,CAST(:raw AS jsonb),NOW())
            """),{"id":hid,"source_url":best[3]["urls"][0] if best[3]["urls"] else None,
                  "ev":" | ".join(best[3]["urls"][:5]),
                  "raw":json.dumps({"phone":best[0],"confidence":best[1],"decision":best[2],"urls":best[3]["urls"][:12]})})
        state="FOUND_HIGH_CONFIDENCE" if best and best[2]=="HIGH_CONFIDENCE" else (
              "CANDIDATES_NEED_VERIFY" if best and best[2]=="NEEDS_VERIFICATION" else (
              "LOW_CONFIDENCE_ONLY" if best else "NO_PHONE_FOUND"))
        c.execute(text("""
          INSERT INTO ai_hospitality_phone_recovery_state_v12418(
            hospitality_id,attempts,last_status,last_error,last_run_at,completed,updated_at
          ) VALUES(:id,1,:st,:err,NOW(),TRUE,NOW())
          ON CONFLICT(hospitality_id) DO UPDATE SET
            attempts=ai_hospitality_phone_recovery_state_v12418.attempts+1,last_status=:st,last_error=:err,
            last_run_at=NOW(),completed=TRUE,updated_at=NOW()
        """),{"id":hid,"st":state,"err":" | ".join(errors)[:1000] or None})
    return {"hospitality_id":hid,"business_name":name,"status":state,
            "best_phone":best[0] if best else None,"confidence":best[1] if best else None}

def _claim_batch(engine,limit):
    """Claim unique IDs once. Previous broken 12.4.17 state is deliberately ignored."""
    ensure_schema(engine)
    with engine.begin() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT e.hospitality_id,e.business_name,e.location,e.city
          FROM ai_hospitality_entity e
          LEFT JOIN ai_hospitality_quality_v12416 q ON q.hospitality_id=e.hospitality_id
          LEFT JOIN ai_hospitality_phone_recovery_state_v12418 s ON s.hospitality_id=e.hospitality_id
          WHERE e.active=TRUE
            AND COALESCE(q.quality_status,'')<>'QUARANTINED_NOISE'
            AND COALESCE(e.contact_phone,'')=''
            AND COALESCE(s.completed,FALSE)=FALSE
          ORDER BY e.hospitality_id
          FOR UPDATE OF e SKIP LOCKED
          LIMIT :lim
        """),{"lim":limit}).mappings().all()]
        for r in rows:
            c.execute(text("""
              INSERT INTO ai_hospitality_phone_recovery_state_v12418(
                hospitality_id,attempts,last_status,last_run_at,completed,updated_at
              ) VALUES(:id,0,'IN_PROGRESS',NOW(),FALSE,NOW())
              ON CONFLICT(hospitality_id) DO UPDATE SET last_status='IN_PROGRESS',last_run_at=NOW(),updated_at=NOW()
            """),{"id":int(r["hospitality_id"])})
    return rows

def run_batch(engine,limit=5):
    limit=max(1,min(int(limit),5))
    rows=_claim_batch(engine,limit)
    results=[]; high=verify=low=none=0
    for r in rows:
        try:
            x=recover_one(engine,r)
        except Exception as exc:
            hid=int(r["hospitality_id"])
            with engine.begin() as c:
                c.execute(text("""
                  UPDATE ai_hospitality_phone_recovery_state_v12418
                  SET attempts=attempts+1,last_status='ERROR',last_error=:err,last_run_at=NOW(),
                      completed=TRUE,updated_at=NOW()
                  WHERE hospitality_id=:id
                """),{"id":hid,"err":f"{type(exc).__name__}: {exc}"[:1000]})
            x={"hospitality_id":hid,"business_name":r.get("business_name"),"status":"ERROR","best_phone":None,"confidence":None}
        results.append(x)
        if x["status"]=="FOUND_HIGH_CONFIDENCE": high+=1
        elif x["status"]=="CANDIDATES_NEED_VERIFY": verify+=1
        elif x["status"]=="LOW_CONFIDENCE_ONLY": low+=1
        elif x["status"]=="NO_PHONE_FOUND": none+=1
    return {"unique_processed":len(rows),"high_confidence_saved":high,"needs_verification":verify,
            "low_confidence":low,"no_phone_found":none,"results":results}

def stats(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        active=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE")).scalar() or 0)
        phones=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE AND COALESCE(contact_phone,'')<>''")).scalar() or 0)
        processed=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_recovery_state_v12418 WHERE completed=TRUE")).scalar() or 0)
        high=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_candidate_v12418 WHERE decision='HIGH_CONFIDENCE'")).scalar() or 0)
        verify=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_candidate_v12418 WHERE decision='NEEDS_VERIFICATION'")).scalar() or 0)
        low=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_candidate_v12418 WHERE decision='LOW_CONFIDENCE'")).scalar() or 0)
        legacy=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_candidate_v12418 WHERE decision='LEGACY_WEAK'")).scalar() or 0)
        noresult=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_recovery_state_v12418 WHERE completed=TRUE AND last_status='NO_PHONE_FOUND'")).scalar() or 0)
        errors=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_phone_recovery_state_v12418 WHERE completed=TRUE AND last_status='ERROR'")).scalar() or 0)
        pending=int(c.execute(text("""
          SELECT COUNT(*)
          FROM ai_hospitality_entity e
          LEFT JOIN ai_hospitality_quality_v12416 q ON q.hospitality_id=e.hospitality_id
          LEFT JOIN ai_hospitality_phone_recovery_state_v12418 s ON s.hospitality_id=e.hospitality_id
          WHERE e.active=TRUE AND COALESCE(q.quality_status,'')<>'QUARANTINED_NOISE'
            AND COALESCE(e.contact_phone,'')='' AND COALESCE(s.completed,FALSE)=FALSE
        """)).scalar() or 0)
    return {"active":active,"phones_saved":phones,"unique_processed":processed,"pending":pending,
            "high_confidence_candidates":high,"needs_verification_candidates":verify,
            "low_confidence_candidates":low,"legacy_weak_candidates":legacy,
            "no_phone_found":noresult,"errors":errors}

def reset_failed_queue(engine):
    """Only resets ERROR or stale IN_PROGRESS rows; successful completed work is never repeated."""
    ensure_schema(engine)
    with engine.begin() as c:
        n=c.execute(text("""
          UPDATE ai_hospitality_phone_recovery_state_v12418
          SET completed=FALSE,last_status='PENDING',last_error=NULL,updated_at=NOW()
          WHERE last_status='ERROR'
             OR (last_status='IN_PROGRESS' AND last_run_at < NOW()-INTERVAL '15 minutes')
        """)).rowcount
    return int(n or 0)

def register(core):
    app=_app(core)
    ensure_schema(core.engine)

    # Remove stale 12.4.17 API routes if present.
    stale={"/api/alliance/hospitality-phone-recovery/quarantine-weak",
           "/api/alliance/hospitality-phone-recovery/batch",
           "/api/alliance/hospitality-phone-recovery/status"}
    for r in list(app.router.routes):
        if getattr(r,"path",None) in stale:
            app.router.routes.remove(r)

    @app.post("/api/alliance/hospitality-phone-recovery/quarantine-weak")
    def quarantine(req:Request):
        _login(core,req)
        moved=quarantine_weak_existing(core.engine)
        return {"status":"PASS","version":VERSION,"moved_to_legacy_weak":0,"safety":"NO_EXISTING_PHONES_MODIFIED",**stats(core.engine)}

    @app.post("/api/alliance/hospitality-phone-recovery/batch")
    def batch(req:Request,limit:int=Query(5)):
        _login(core,req)
        return {"status":"PASS","version":VERSION,**run_batch(core.engine,limit),**stats(core.engine)}

    @app.post("/api/alliance/hospitality-phone-recovery/reset-errors")
    def reset_errors(req:Request):
        _login(core,req)
        n=reset_failed_queue(core.engine)
        return {"status":"PASS","reset_rows":n,**stats(core.engine)}

    @app.get("/api/alliance/hospitality-phone-recovery/status")
    def status_api(req:Request):
        _login(core,req)
        return {"status":"PASS","version":VERSION,**stats(core.engine),
                "queue_rule":"Each hospitality ID is processed once in 12.4.18 unless an ERROR is explicitly reset",
                "auto_save_rule":"HIGH_CONFIDENCE only: >=80 score, >=2 matched domains, >=1 strong domain",
                "single_source_phone_auto_saved":False}

    # Replace Hospitality GET wrappers with a clean wrapper around the underlying 12.4.16 page.
    old=[]
    for r in list(app.router.routes):
        if getattr(r,"path",None) in {"/hospitality-intelligence","/v3/hospitality-intelligence"} and "GET" in set(getattr(r,"methods",set()) or set()):
            old.append(r)
    if old:
        # Prefer endpoint from the purity module, not the already-wrapped 12.4.17 endpoint.
        original=None
        try:
            import alliance_hospitality_purity_v12416 as purity
            original=getattr(purity,"render_page",None)
        except Exception:
            original=None
        if not original:
            original=old[0].endpoint
        for r in old: app.router.routes.remove(r)

        def _render(req,status,page,per_page,msg):
            try:
                return original(core,req,status,page,per_page,msg)
            except TypeError:
                try:
                    return original(req,status=status,page_no=page,per_page=per_page,msg=msg)
                except TypeError:
                    return original(req,status=status,page=page,per_page=per_page,msg=msg)

        @app.get("/hospitality-intelligence",response_class=HTMLResponse)
        @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
        def page(req:Request,status:str=Query("CALL_READY"),page:int=Query(1),per_page:int=Query(100),msg:str=Query("")):
            resp=_render(req,status,page,per_page,msg)
            try:
                body=resp.body.decode("utf-8")
                s=stats(core.engine)
                panel=f"""
                <div class='card' id='phone-recovery-v12418'>
                  <h2>All Numbers Recovery 12.4.18 · Controlled Queue</h2>
                  <p>Existing phone values are preserved. Only businesses missing a phone are searched. Each business is attempted once. One-source numbers are review-only. Only corroborated high-confidence numbers are auto-saved.</p>
                  <div class='grid'>
                    <div class='m'>Unique Businesses Attempted<strong id='pr-processed'>{s['unique_processed']}</strong></div>
                    <div class='m'>Phones Saved<strong id='pr-saved'>{s['phones_saved']}</strong></div>
                    <div class='m'>High Confidence<strong id='pr-high'>{s['high_confidence_candidates']}</strong></div>
                    <div class='m'>Needs Verification<strong id='pr-verify'>{s['needs_verification_candidates']}</strong></div>
                    <div class='m'>No Phone Found<strong id='pr-none'>{s['no_phone_found']}</strong></div>
                    <div class='m'>Remaining<strong id='pr-pending'>{s['pending']}</strong></div>
                    <div class='m'>Legacy Weak<strong id='pr-legacy'>{s['legacy_weak_candidates']}</strong></div>
                    <div class='m'>Errors<strong id='pr-errors'>{s['errors']}</strong></div>
                  </div><br>
                  <button type='button' onclick='qWeak()'>A. Existing Phones Preserved</button>
                  <button type='button' onclick='testFive()'>B. Test Next 5</button>
                  <button type='button' onclick='recoverAll()'>C. Recover All Remaining</button>
                  <button type='button' onclick='resetErrors()'>Reset Errors Only</button>
                  <div id='pr-progress' style='margin-top:10px;font-weight:700'></div>
                  <script>
                  let PR_STOP=false;
                  function paint(j){{
                    let map={{'pr-processed':'unique_processed','pr-saved':'phones_saved','pr-high':'high_confidence_candidates',
                      'pr-verify':'needs_verification_candidates','pr-none':'no_phone_found','pr-pending':'pending',
                      'pr-legacy':'legacy_weak_candidates','pr-errors':'errors'}};
                    for(let id in map){{let e=document.getElementById(id); if(e) e.innerText=j[map[id]] ?? e.innerText;}}
                  }}
                  async function postu(url){{
                    let r=await fetch(url,{{method:'POST'}});
                    let j=await r.json(); paint(j); return j;
                  }}
                  async function qWeak(){{
                    let j=await postu('/api/alliance/hospitality-phone-recovery/quarantine-weak');
                    document.getElementById('pr-progress').innerText='Safety check complete. Existing phones preserved. No phone values changed.';
                  }}
                  async function testFive(){{
                    let j=await postu('/api/alliance/hospitality-phone-recovery/batch?limit=5');
                    document.getElementById('pr-progress').innerText='Tested '+j.unique_processed+' unique businesses. High '+j.high_confidence_saved+', verify '+j.needs_verification+', low '+j.low_confidence+', no phone '+j.no_phone_found+'.';
                  }}
                  async function recoverAll(){{
                    PR_STOP=false; let el=document.getElementById('pr-progress');
                    while(!PR_STOP){{
                      let j=await postu('/api/alliance/hospitality-phone-recovery/batch?limit=5');
                      el.innerText='Attempted '+j.unique_processed+' this batch | total '+j.unique_processed+'? refresh metrics above | remaining '+j.pending;
                      if((j.unique_processed||0)===0 || (j.pending||0)===0) break;
                      await new Promise(r=>setTimeout(r,900));
                    }}
                    el.innerText='Recovery stopped/completed. Remaining: '+document.getElementById('pr-pending').innerText+'.';
                  }}
                  async function resetErrors(){{
                    let j=await postu('/api/alliance/hospitality-phone-recovery/reset-errors');
                    document.getElementById('pr-progress').innerText='Reset '+j.reset_rows+' failed/stale queue rows.';
                  }}
                  </script>
                </div>
                """
                marker="<div class='card'><h2>Database Repair Controls</h2>"
                if marker in body:
                    body=body.replace(marker,panel+marker,1)
                    # Disable/remove old unsafe enrichment action and misleading old banner.
                    body=body.replace("3. Enrich Next 20 Missing Contacts","3. OLD ENRICHMENT DISABLED")
                    body=body.replace("Enrichment uses public web evidence through the configured LangSearch provider. New contact details remain UNVERIFIED until human verification.",
                                      "Old loose enrichment is disabled. Use All Numbers Recovery 12.4.18 above.")
                    body=re.sub(r"<form[^>]*action=['\"]/hospitality-intelligence/run-enrichment['\"][\\s\\S]*?</form>","",body,count=1,flags=re.I)
                    # Remove any old 12.4.17 panel if nested.
                    body=re.sub(r"<div class='card'>\\s*<h2>All Numbers Recovery · Strict Cross-Check</h2>[\\s\\S]*?</div>\\s*</div>","",body,count=1,flags=re.I)
                    return HTMLResponse(body,headers={"Cache-Control":"no-store"})
            except Exception:
                pass
            return resp

    return {"status":"AUTHORITATIVE","version":VERSION,"unique_queue":True,
            "batch_size":5,"old_enrichment_disabled":True,"auto_save_high_confidence_only":True}
