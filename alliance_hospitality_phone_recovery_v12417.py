from __future__ import annotations
import html, json, re
from urllib.parse import urlparse
from fastapi import Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION="12.4.17-ALL-NUMBERS-RECOVERY-STRICT"

BAD_NUMBERS={"9876543210","9999999999","8888888888","1234567890","0000000000"}
AGGREGATORS=("justdial.","tripadvisor.","zomato.","swiggy.","magicpin.","sloshout.","wedmegood.","wanderlog.","agoda.","google.","so.city","threebestrated.")

def _app(core): return getattr(core,"app",None) or core
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip())
def _phone(v):
    d=re.sub(r"\D","",str(v or ""))
    if len(d)>=10:
        d=d[-10:]
        if len(d)==10 and d[0] in "6789" and len(set(d))>=5 and d not in BAD_NUMBERS:
            return d
    return None
def _tokens(s):
    return {x for x in re.findall(r"[a-z0-9]+",_norm(s).lower()) if len(x)>=3}
def _host(url):
    try: return urlparse(_norm(url)).netloc.lower().replace("www.","")
    except Exception: return ""
def _is_aggregator(host):
    return any(x in host for x in AGGREGATORS)
def _name_match(name,textv):
    a=_tokens(name); b=_tokens(textv)
    return bool(a) and len(a & b)/max(1,len(a)) >= 0.5
def _location_match(location,textv):
    a=_tokens(location)
    if not a: return True
    b=_tokens(textv)
    return len(a & b)>=1
def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_phone_candidate_v12417(
          candidate_id BIGSERIAL PRIMARY KEY,
          hospitality_id BIGINT NOT NULL REFERENCES ai_hospitality_entity(hospitality_id),
          phone TEXT NOT NULL,
          confidence INT NOT NULL DEFAULT 0,
          decision TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION',
          evidence_count INT NOT NULL DEFAULT 0,
          domain_count INT NOT NULL DEFAULT 0,
          evidence_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
          source_summary TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(hospitality_id,phone)
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_phone_recovery_state_v12417(
          hospitality_id BIGINT PRIMARY KEY REFERENCES ai_hospitality_entity(hospitality_id),
          attempts INT NOT NULL DEFAULT 0,
          last_status TEXT NOT NULL DEFAULT 'PENDING',
          last_error TEXT,
          last_run_at TIMESTAMPTZ,
          completed BOOLEAN NOT NULL DEFAULT FALSE,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_hosp_phone_candidate_decision_v12417 ON ai_hospitality_phone_candidate_v12417(decision)"))
    return True

def quarantine_weak_existing(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT e.hospitality_id,e.contact_phone,e.verification_status
          FROM ai_hospitality_entity e
          WHERE e.active=TRUE AND COALESCE(e.contact_phone,'')<>''
            AND COALESCE(e.verification_status,'UNVERIFIED')<>'VERIFIED'
            AND EXISTS(
              SELECT 1 FROM ai_hospitality_source_history s
              WHERE s.hospitality_id=e.hospitality_id
                AND s.source_type='CONTACT_ENRICHMENT'
                AND s.source_name='LANGSEARCH'
            )
        """)).mappings().all()]
    moved=0
    with engine.begin() as c:
        for r in rows:
            p=_phone(r.get("contact_phone"))
            if p:
                c.execute(text("""
                  INSERT INTO ai_hospitality_phone_candidate_v12417(
                    hospitality_id,phone,confidence,decision,evidence_count,domain_count,source_summary,updated_at
                  ) VALUES(:id,:p,20,'LEGACY_WEAK',1,0,'Migrated from weak 12.4.16 enrichment',NOW())
                  ON CONFLICT(hospitality_id,phone) DO UPDATE SET decision='LEGACY_WEAK',updated_at=NOW()
                """),{"id":int(r["hospitality_id"]),"p":p})
            c.execute(text("UPDATE ai_hospitality_entity SET contact_phone=NULL,updated_at=NOW() WHERE hospitality_id=:id"),{"id":int(r["hospitality_id"])})
            moved+=1
    try:
        import alliance_hospitality_purity_v12416 as p
        p.audit_purity(engine)
    except Exception:
        pass
    return moved

def _collect_candidates(name,location,results):
    phones={}
    official_existing=set()
    for item in results:
        url=_norm(item.get("url")); host=_host(url)
        textv=" ".join([_norm(item.get("name")),_norm(item.get("summary")),_norm(item.get("snippet")),url])
        if not _name_match(name,textv): continue
        if not _location_match(location,textv): continue
        found=set()
        for raw in re.findall(r"(?:\+?91[\s().-]*)?[6-9](?:[\s().-]*\d){9,12}",textv):
            p=_phone(raw)
            if p: found.add(p)
        for p in found:
            d=phones.setdefault(p,{"urls":[],"domains":set(),"nonagg":0,"evidence":0})
            d["evidence"]+=1
            if url: d["urls"].append(url)
            if host:
                d["domains"].add(host)
                if not _is_aggregator(host): d["nonagg"]+=1
    return phones

def _score_candidate(data):
    domains=len(data["domains"]); ev=int(data["evidence"]); nonagg=int(data["nonagg"])
    score=25
    if ev>=2: score+=25
    if domains>=2: score+=25
    if nonagg>=1: score+=15
    if nonagg>=2: score+=10
    score=min(score,100)
    if score>=80 and domains>=2:
        decision="HIGH_CONFIDENCE"
    elif score>=60:
        decision="NEEDS_VERIFICATION"
    else:
        decision="LOW_CONFIDENCE"
    return score,decision

def recover_one(engine,row):
    import alliance_v31_hospitality as h
    hid=int(row["hospitality_id"]); name=_norm(row["business_name"]); location=_norm(row.get("location") or row.get("city") or "Delhi NCR")
    queries=[
      f'"{name}" "{location}" phone contact number',
      f'"{name}" "{location}" mobile whatsapp',
      f'"{name}" "{location}" official website contact'
    ]
    merged=[]
    provider_errors=[]
    for q in queries:
        r=h._langsearch(q,8)
        if r.get("status")=="OK":
            merged.extend(r.get("results",[]))
        else:
            provider_errors.append(r.get("message") or r.get("status"))
    candidates=_collect_candidates(name,location,merged)
    best=None
    with engine.begin() as c:
        for p,d in candidates.items():
            score,decision=_score_candidate(d)
            c.execute(text("""
              INSERT INTO ai_hospitality_phone_candidate_v12417(
                hospitality_id,phone,confidence,decision,evidence_count,domain_count,evidence_urls,source_summary,updated_at
              ) VALUES(:id,:p,:score,:decision,:ev,:domains,CAST(:urls AS jsonb),:summary,NOW())
              ON CONFLICT(hospitality_id,phone) DO UPDATE SET
                confidence=GREATEST(ai_hospitality_phone_candidate_v12417.confidence,EXCLUDED.confidence),
                decision=CASE WHEN EXCLUDED.confidence>ai_hospitality_phone_candidate_v12417.confidence
                              THEN EXCLUDED.decision ELSE ai_hospitality_phone_candidate_v12417.decision END,
                evidence_count=GREATEST(ai_hospitality_phone_candidate_v12417.evidence_count,EXCLUDED.evidence_count),
                domain_count=GREATEST(ai_hospitality_phone_candidate_v12417.domain_count,EXCLUDED.domain_count),
                evidence_urls=EXCLUDED.evidence_urls,source_summary=EXCLUDED.source_summary,updated_at=NOW()
            """),{"id":hid,"p":p,"score":score,"decision":decision,"ev":d["evidence"],"domains":len(d["domains"]),
                  "urls":json.dumps(d["urls"][:12]),"summary":f"{d['evidence']} matched appearances across {len(d['domains'])} domains"})
            if best is None or score>best[1]: best=(p,score,decision,d)
        if best and best[2]=="HIGH_CONFIDENCE":
            c.execute(text("""
              UPDATE ai_hospitality_entity
              SET contact_phone=:p,updated_at=NOW()
              WHERE hospitality_id=:id AND COALESCE(contact_phone,'')=''
            """),{"p":best[0],"id":hid})
            c.execute(text("""
              INSERT INTO ai_hospitality_source_history(
                hospitality_id,source_type,source_name,evidence_text,raw_payload,seen_at
              ) VALUES(:id,'STRICT_PHONE_RECOVERY','LANGSEARCH_CROSSCHECK',:ev,CAST(:raw AS jsonb),NOW())
            """),{"id":hid,"ev":" | ".join(best[3]["urls"][:5]),
                  "raw":json.dumps({"phone":best[0],"confidence":best[1],"decision":best[2],"urls":best[3]["urls"][:12]})})
        status="FOUND_HIGH_CONFIDENCE" if best and best[2]=="HIGH_CONFIDENCE" else ("CANDIDATES_NEED_VERIFY" if best else "NO_PHONE_FOUND")
        c.execute(text("""
          INSERT INTO ai_hospitality_phone_recovery_state_v12417(
            hospitality_id,attempts,last_status,last_error,last_run_at,completed,updated_at
          ) VALUES(:id,1,:st,:err,NOW(),TRUE,NOW())
          ON CONFLICT(hospitality_id) DO UPDATE SET attempts=ai_hospitality_phone_recovery_state_v12417.attempts+1,
            last_status=:st,last_error=:err,last_run_at=NOW(),completed=TRUE,updated_at=NOW()
        """),{"id":hid,"st":status,"err":" | ".join(provider_errors)[:1000] or None})
    return {"hospitality_id":hid,"status":status,"best_phone":best[0] if best else None,"confidence":best[1] if best else None}

def run_batch(engine,limit=10):
    ensure_schema(engine)
    limit=max(1,min(int(limit),20))
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT e.hospitality_id,e.business_name,e.location,e.city,e.contact_phone
          FROM ai_hospitality_entity e
          LEFT JOIN ai_hospitality_quality_v12416 q ON q.hospitality_id=e.hospitality_id
          LEFT JOIN ai_hospitality_phone_recovery_state_v12417 s ON s.hospitality_id=e.hospitality_id
          WHERE e.active=TRUE
            AND COALESCE(q.quality_status,'')<>'QUARANTINED_NOISE'
            AND COALESCE(e.contact_phone,'')=''
            AND COALESCE(s.completed,FALSE)=FALSE
          ORDER BY e.hospitality_id
          LIMIT :lim
        """),{"lim":limit}).mappings().all()]
    out=[]; high=0; verify=0
    for r in rows:
        x=recover_one(engine,r); out.append(x)
        if x["status"]=="FOUND_HIGH_CONFIDENCE": high+=1
        elif x["status"]=="CANDIDATES_NEED_VERIFY": verify+=1
    try:
        import alliance_hospitality_purity_v12416 as p
        p.audit_purity(engine)
    except Exception:
        pass
    return {"processed":len(rows),"high_confidence_saved":high,"need_verification":verify,"results":out}

def stats(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        r=dict(c.execute(text("""
          SELECT
            COUNT(*) FILTER(WHERE e.active=TRUE) active,
            COUNT(*) FILTER(WHERE e.active=TRUE AND COALESCE(e.contact_phone,'')<>'') phones_saved,
            COUNT(*) FILTER(WHERE c.decision='HIGH_CONFIDENCE') high_confidence_candidates,
            COUNT(*) FILTER(WHERE c.decision='NEEDS_VERIFICATION') needs_verification_candidates,
            COUNT(*) FILTER(WHERE c.decision='LOW_CONFIDENCE') low_confidence_candidates,
            COUNT(*) FILTER(WHERE c.decision='LEGACY_WEAK') legacy_weak_candidates,
            COUNT(DISTINCT s.hospitality_id) FILTER(WHERE s.completed=TRUE) processed
          FROM ai_hospitality_entity e
          LEFT JOIN ai_hospitality_phone_candidate_v12417 c ON c.hospitality_id=e.hospitality_id
          LEFT JOIN ai_hospitality_phone_recovery_state_v12417 s ON s.hospitality_id=e.hospitality_id
        """)).mappings().one())
        pending=int(c.execute(text("""
          SELECT COUNT(*) FROM ai_hospitality_entity e
          LEFT JOIN ai_hospitality_quality_v12416 q ON q.hospitality_id=e.hospitality_id
          LEFT JOIN ai_hospitality_phone_recovery_state_v12417 s ON s.hospitality_id=e.hospitality_id
          WHERE e.active=TRUE AND COALESCE(q.quality_status,'')<>'QUARANTINED_NOISE'
            AND COALESCE(e.contact_phone,'')='' AND COALESCE(s.completed,FALSE)=FALSE
        """)).scalar() or 0)
    r["pending"]=pending
    return r

def register(core):
    app=_app(core); ensure_schema(core.engine)

    @app.post("/api/alliance/hospitality-phone-recovery/quarantine-weak")
    def quarantine(req:Request):
        _login(core,req)
        moved=quarantine_weak_existing(core.engine)
        return {"status":"PASS","moved_to_legacy_weak":moved,**stats(core.engine)}

    @app.post("/api/alliance/hospitality-phone-recovery/batch")
    def batch(req:Request,limit:int=Query(10)):
        _login(core,req)
        return {"status":"PASS","version":VERSION,**run_batch(core.engine,limit),**stats(core.engine)}

    @app.get("/api/alliance/hospitality-phone-recovery/status")
    def status(req:Request):
        _login(core,req)
        return {"status":"PASS","version":VERSION,**stats(core.engine),
                "strict_rule":"Auto-save only when phone is corroborated across >=2 matched domains with confidence >=80",
                "weak_matches_auto_saved":False}

    # Inject controls into current Hospitality UI without replacing its database table logic.
    old_routes=[]
    for r in list(app.router.routes):
        if getattr(r,"path",None) in {"/hospitality-intelligence","/v3/hospitality-intelligence"} and "GET" in set(getattr(r,"methods",set()) or set()):
            old_routes.append(r)
    if old_routes:
        original=old_routes[-1].endpoint
        for r in old_routes: app.router.routes.remove(r)

        @app.get("/hospitality-intelligence",response_class=HTMLResponse)
        @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
        def wrapped(req:Request,status:str=Query("CALL_READY"),page:int=Query(1),per_page:int=Query(100),msg:str=Query("")):
            resp=original(req,status=status,page_no=page,per_page=per_page,msg=msg)
            try:
                body=resp.body.decode("utf-8")
                s=stats(core.engine)
                panel=f"""
                <div class='card'>
                  <h2>All Numbers Recovery · Strict Cross-Check</h2>
                  <p>Auto-saves only high-confidence phone numbers corroborated across multiple matched web domains. Ambiguous numbers go to verification, not Master.</p>
                  <div class='grid'>
                    <div class='m'>Phones Saved<strong>{s['phones_saved']}</strong></div>
                    <div class='m'>Pending<strong id='phone-pending'>{s['pending']}</strong></div>
                    <div class='m'>High Confidence Candidates<strong>{s['high_confidence_candidates']}</strong></div>
                    <div class='m'>Needs Verification<strong>{s['needs_verification_candidates']}</strong></div>
                    <div class='m'>Legacy Weak<strong>{s['legacy_weak_candidates']}</strong></div>
                  </div><br>
                  <button type='button' onclick='quarantineWeak()'>A. Quarantine Weak Existing Phones</button>
                  <button type='button' onclick='recoverAllPhones()'>B. Recover ALL Numbers</button>
                  <div id='phone-progress' style='margin-top:10px;font-weight:700'></div>
                  <script>
                  async function quarantineWeak(){{
                    let x=await fetch('/api/alliance/hospitality-phone-recovery/quarantine-weak',{{method:'POST'}});
                    let j=await x.json(); document.getElementById('phone-progress').innerText='Weak numbers quarantined: '+j.moved_to_legacy_weak+'. Pending: '+j.pending;
                  }}
                  async function recoverAllPhones(){{
                    let el=document.getElementById('phone-progress'); let total=0,high=0,verify=0;
                    while(true){{
                      el.innerText='Recovering... processed '+total+' records';
                      let x=await fetch('/api/alliance/hospitality-phone-recovery/batch?limit=10',{{method:'POST'}});
                      let j=await x.json();
                      if(j.status!=='PASS'){{el.innerText='Stopped: '+JSON.stringify(j);break;}}
                      total+=j.processed||0; high+=j.high_confidence_saved||0; verify+=j.need_verification||0;
                      document.getElementById('phone-pending').innerText=j.pending;
                      el.innerText='Processed '+total+' | high-confidence saved '+high+' | needs verification '+verify+' | pending '+j.pending;
                      if((j.processed||0)===0 || (j.pending||0)===0) break;
                      await new Promise(r=>setTimeout(r,700));
                    }}
                    el.innerText+=' | Complete. Refresh page.';
                  }}
                  </script>
                </div>
                """
                marker="<div class='card'><h2>Database Repair Controls</h2>"
                if marker in body:
                    body=body.replace(marker,panel+marker,1)
                    return HTMLResponse(body,headers={"Cache-Control":"no-store"})
            except Exception:
                pass
            return resp

    return {"status":"AUTHORITATIVE","version":VERSION,"all_numbers_recovery":True,"auto_save_rule":">=80 confidence + >=2 matched domains"}
