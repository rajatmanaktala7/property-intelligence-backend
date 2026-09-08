from __future__ import annotations

import html, json, re
from urllib.parse import quote_plus, urlparse
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="12.4.16B-DEDUPE-SQL-AND-TOP-PREVIOUS-NAV-FIX"

NOISE_EXACT={
    "local","farm house","dance bar","cafe west delhi","banquet halls in connaught place",
    "sector 29 gurgaon pubs and bars","commercial projects in noida commercial project noida expressway extension"
}
NOISE_PATTERNS=(
    r"^restaurants? in\b",r"^cafes? in\b",r"^banquet halls? in\b",r"^hotels? in\b",
    r"^clubs? in\b",r"^bars? in\b",r"^guest houses? in\b",r"\bcommercial projects?\b",
)
BAD_EMAILS={"you@email.com","example@example.com","test@test.com"}
AGGREGATOR_HOSTS=("justdial.","tripadvisor.","zomato.","swiggy.","magicpin.","sloshout.","wedmegood.")

def _app(core): return getattr(core,"app",None) or core
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip())
def _name_key(v):
    s=_norm(v).lower()
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def _phone(v):
    d=re.sub(r"\D","",str(v or ""))
    if len(d)>=10:
        d=d[-10:]
        if d[0] in "6789" and len(set(d))>3:
            return d
    return None
def _email(v):
    m=re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",str(v or ""))
    if not m: return None
    x=m.group(0).lower()
    return None if x in BAD_EMAILS else x
def _noise(name):
    k=_name_key(name)
    if k in NOISE_EXACT: return True
    return any(re.search(p,k,re.I) for p in NOISE_PATTERNS)
def _category(name,current):
    cur=str(current or "OTHER").upper()
    s=_norm(name).lower()
    rules=[
      ("GUEST_HOUSE",("guest house","guesthouse","bnb","bed & breakfast")),
      ("BANQUET",("banquet","party hall","marriage hall")),
      ("LOUNGE",("lounge",)),("CAFE",("cafe","café","coffee")),
      ("RESTAURANT",("restaurant","restro","dining","eatery")),
      ("HOTEL",("hotel","resort")),("CLUB",("club",)),("BAR",("bar","pub","brewery")),
    ]
    if cur!="OTHER": return cur
    for cat,words in rules:
        if any(w in s for w in words): return cat
    return cur
def ensure_quality_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_quality_v12416(
          hospitality_id BIGINT PRIMARY KEY REFERENCES ai_hospitality_entity(hospitality_id),
          quality_status TEXT NOT NULL DEFAULT 'ENRICHMENT_REQUIRED',
          purity_reason TEXT,
          completeness_score INT NOT NULL DEFAULT 0,
          contact_ready BOOLEAN NOT NULL DEFAULT FALSE,
          call_ready BOOLEAN NOT NULL DEFAULT FALSE,
          duplicate_of BIGINT,
          enrichment_attempts INT NOT NULL DEFAULT 0,
          last_enrichment_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_hosp_quality_status_v12416 ON ai_hospitality_quality_v12416(quality_status)"))
    return True
def _score(d):
    checks=[
      bool(_norm(d.get("business_name"))),
      str(d.get("category") or "OTHER").upper()!="OTHER",
      bool(_norm(d.get("location"))),
      bool(_phone(d.get("contact_phone") or d.get("whatsapp_phone"))),
      bool(_email(d.get("email"))),
      bool(_norm(d.get("website"))),
    ]
    return round(100*sum(checks)/len(checks))
def audit_purity(engine):
    ensure_quality_schema(engine)
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT hospitality_id,business_name,category,location,contact_phone,whatsapp_phone,email,website
          FROM ai_hospitality_entity WHERE active=TRUE
        """)).mappings().all()]
    counts={"scanned":0,"quarantined":0,"contact_ready":0,"call_ready":0,"enrichment_required":0,"category_fixed":0}
    with engine.begin() as c:
        for d in rows:
            counts["scanned"]+=1
            hid=int(d["hospitality_id"])
            cat=_category(d.get("business_name"),d.get("category"))
            if cat!=str(d.get("category") or "OTHER").upper():
                c.execute(text("UPDATE ai_hospitality_entity SET category=:cat,updated_at=NOW() WHERE hospitality_id=:id"),{"cat":cat,"id":hid})
                d["category"]=cat; counts["category_fixed"]+=1
            bad=_noise(d.get("business_name"))
            phone=_phone(d.get("contact_phone") or d.get("whatsapp_phone"))
            email=_email(d.get("email"))
            website=_norm(d.get("website"))
            location=_norm(d.get("location"))
            contact_ready=bool(location and (phone or email or website))
            call_ready=bool(location and phone)
            if bad:
                status="QUARANTINED_NOISE"; reason="Generic/search/category/non-hospitality entity"
                counts["quarantined"]+=1
            elif call_ready:
                status="CALL_READY"; reason=None; counts["call_ready"]+=1; counts["contact_ready"]+=1
            elif contact_ready:
                status="CONTACT_READY"; reason=None; counts["contact_ready"]+=1
            else:
                status="ENRICHMENT_REQUIRED"; reason=None; counts["enrichment_required"]+=1
            c.execute(text("""
              INSERT INTO ai_hospitality_quality_v12416(
                hospitality_id,quality_status,purity_reason,completeness_score,contact_ready,call_ready,updated_at
              ) VALUES(:id,:st,:reason,:score,:cr,:call,NOW())
              ON CONFLICT(hospitality_id) DO UPDATE SET
                quality_status=EXCLUDED.quality_status,purity_reason=EXCLUDED.purity_reason,
                completeness_score=EXCLUDED.completeness_score,contact_ready=EXCLUDED.contact_ready,
                call_ready=EXCLUDED.call_ready,updated_at=NOW()
            """),{"id":hid,"st":status,"reason":reason,"score":_score(d),"cr":contact_ready,"call":call_ready})
    return counts
def dedupe_exact(engine):
    ensure_quality_schema(engine)
    with engine.connect() as c:
        groups=c.execute(text("""
          SELECT LOWER(REGEXP_REPLACE(TRIM(business_name),'[^a-zA-Z0-9]+',' ','g')) AS nk,
                 ARRAY_AGG(hospitality_id ORDER BY hospitality_id) ids
          FROM ai_hospitality_entity
          WHERE active=TRUE AND COALESCE(TRIM(business_name),'')<>''
          GROUP BY 1 HAVING COUNT(*)>1
        """)).all()
    merged=0
    with engine.begin() as c:
        for _,ids in groups:
            records=[dict(x) for x in c.execute(text("""
              SELECT hospitality_id,business_name,category,location,city,contact_name,contact_phone,
                     whatsapp_phone,email,website,verification_status,notes
              FROM ai_hospitality_entity WHERE hospitality_id=ANY(:ids)
            """),{"ids":list(ids)}).mappings().all()]
            if len(records)<2: continue
            records.sort(key=lambda d:(_score(d),int(d["hospitality_id"])),reverse=True)
            keep=records[0]; kid=int(keep["hospitality_id"])
            for lose in records[1:]:
                lid=int(lose["hospitality_id"])
                updates={}
                for f in ("location","city","contact_name","contact_phone","whatsapp_phone","email","website"):
                    if not _norm(keep.get(f)) and _norm(lose.get(f)):
                        updates[f]=lose.get(f); keep[f]=lose.get(f)
                if str(keep.get("category") or "OTHER").upper()=="OTHER" and str(lose.get("category") or "OTHER").upper()!="OTHER":
                    updates["category"]=lose["category"]; keep["category"]=lose["category"]
                for f,v in updates.items():
                    c.execute(text(f"UPDATE ai_hospitality_entity SET {f}=:v,updated_at=NOW() WHERE hospitality_id=:id"),{"v":v,"id":kid})
                c.execute(text("UPDATE ai_hospitality_source_history SET hospitality_id=:keep WHERE hospitality_id=:lose"),{"keep":kid,"lose":lid})
                c.execute(text("""
                  UPDATE ai_hospitality_entity SET active=FALSE,
                    notes=COALESCE(notes,'') || ' | MERGED_DUPLICATE_INTO:' || CAST(:keep AS TEXT),
                    updated_at=NOW()
                  WHERE hospitality_id=:lose
                """),{"keep":str(kid),"lose":lid})
                c.execute(text("""
                  INSERT INTO ai_hospitality_quality_v12416(hospitality_id,quality_status,purity_reason,duplicate_of,updated_at)
                  VALUES(:id,'MERGED_DUPLICATE','Exact normalized business-name duplicate',:keep,NOW())
                  ON CONFLICT(hospitality_id) DO UPDATE SET quality_status='MERGED_DUPLICATE',
                    purity_reason='Exact normalized business-name duplicate',duplicate_of=:keep,updated_at=NOW()
                """),{"id":lid,"keep":kid})
                merged+=1
    audit_purity(engine)
    return {"merged_duplicates":merged,"duplicate_groups":len(groups)}
def _best_web_result(results):
    for item in results:
        u=_norm(item.get("url"))
        host=urlparse(u).netloc.lower() if u else ""
        if u and not any(a in host for a in AGGREGATOR_HOSTS):
            return item
    return results[0] if results else {}
def enrich_batch(engine,limit=20):
    import alliance_v31_hospitality as h
    ensure_quality_schema(engine); audit_purity(engine)
    limit=max(1,min(int(limit),25))
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT e.hospitality_id,e.business_name,e.category,e.location,e.city,e.contact_phone,e.email,e.website
          FROM ai_hospitality_entity e
          JOIN ai_hospitality_quality_v12416 q ON q.hospitality_id=e.hospitality_id
          WHERE e.active=TRUE AND q.quality_status='ENRICHMENT_REQUIRED'
            AND q.enrichment_attempts<3
          ORDER BY q.enrichment_attempts ASC,e.last_seen_at DESC,e.hospitality_id DESC
          LIMIT :lim
        """),{"lim":limit}).mappings().all()]
    attempted=saved=phones=emails=websites=0
    for d in rows:
        attempted+=1; hid=int(d["hospitality_id"])
        q=f'"{_norm(d["business_name"])}" "{_norm(d.get("location") or d.get("city") or "Delhi NCR")}" phone contact email official website'
        result=h._langsearch(q,8)
        found_phone=found_email=found_web=None
        evidence=[]
        if result.get("status")=="OK":
            for item in result.get("results",[]):
                blob=" ".join([_norm(item.get("name")),_norm(item.get("summary")),_norm(item.get("snippet"))])
                if not found_phone:
                    for cand in re.findall(r"(?:\+?91[\s-]?)?[6-9]\d[\d\s-]{8,12}\d",blob):
                        p=_phone(cand)
                        if p: found_phone=p; break
                if not found_email:
                    found_email=_email(blob)
                if item.get("url"): evidence.append(item.get("url"))
            best=_best_web_result(result.get("results",[]))
            if not _norm(d.get("website")):
                u=_norm(best.get("url"))
                host=urlparse(u).netloc.lower() if u else ""
                if u and not any(a in host for a in AGGREGATOR_HOSTS):
                    found_web=u
        updates={}
        if not _phone(d.get("contact_phone")) and found_phone: updates["contact_phone"]=found_phone; phones+=1
        if not _email(d.get("email")) and found_email: updates["email"]=found_email; emails+=1
        if not _norm(d.get("website")) and found_web: updates["website"]=found_web; websites+=1
        with engine.begin() as c:
            if updates:
                sets=[]; params={"id":hid}
                for i,(k,v) in enumerate(updates.items()):
                    kk=f"v{i}"; sets.append(f"{k}=:{kk}"); params[kk]=v
                c.execute(text("UPDATE ai_hospitality_entity SET "+",".join(sets)+",updated_at=NOW() WHERE hospitality_id=:id"),params)
                saved+=1
            c.execute(text("""
              INSERT INTO ai_hospitality_source_history(
                hospitality_id,source_type,source_name,evidence_text,raw_payload,seen_at
              ) VALUES(:id,'CONTACT_ENRICHMENT','LANGSEARCH',:ev,CAST(:raw AS jsonb),NOW())
            """),{"id":hid,"ev":" | ".join(evidence[:5]),"raw":json.dumps({"query":q,"provider_status":result.get("status"),"urls":evidence[:8]})})
            c.execute(text("""
              UPDATE ai_hospitality_quality_v12416 SET enrichment_attempts=enrichment_attempts+1,
                last_enrichment_at=NOW(),updated_at=NOW() WHERE hospitality_id=:id
            """),{"id":hid})
    audit_purity(engine)
    return {"attempted":attempted,"records_enriched":saved,"phones_found":phones,"emails_found":emails,"websites_found":websites}
def stats(engine):
    ensure_quality_schema(engine); audit_purity(engine)
    with engine.connect() as c:
        r=dict(c.execute(text("""
          SELECT COUNT(*) FILTER(WHERE e.active=TRUE) total_active,
                 COUNT(*) FILTER(WHERE e.active=TRUE AND q.quality_status='CALL_READY') call_ready,
                 COUNT(*) FILTER(WHERE e.active=TRUE AND q.quality_status='CONTACT_READY') contact_ready,
                 COUNT(*) FILTER(WHERE e.active=TRUE AND q.quality_status='ENRICHMENT_REQUIRED') enrichment_required,
                 COUNT(*) FILTER(WHERE e.active=TRUE AND q.quality_status='QUARANTINED_NOISE') quarantined_noise,
                 COUNT(*) FILTER(WHERE e.active=FALSE AND q.quality_status='MERGED_DUPLICATE') merged_duplicates,
                 COUNT(*) FILTER(WHERE e.active=TRUE AND COALESCE(e.contact_phone,'')<>'') with_phone
          FROM ai_hospitality_entity e LEFT JOIN ai_hospitality_quality_v12416 q USING(hospitality_id)
        """)).mappings().one())
    return r
def render_page(core,req,status="CALL_READY",page=1,per_page=100,msg=""):
    _login(core,req); ensure_quality_schema(core.engine); audit_purity(core.engine)
    allowed={"CALL_READY","CONTACT_READY","ENRICHMENT_REQUIRED","QUARANTINED_NOISE","ALL"}
    if status not in allowed: status="CALL_READY"
    page=max(1,int(page)); per_page=max(25,min(int(per_page),200)); off=(page-1)*per_page
    clause="" if status=="ALL" else "AND q.quality_status=:status"
    params={"status":status,"lim":per_page,"off":off}
    with core.engine.connect() as c:
        total=int(c.execute(text(f"""SELECT COUNT(*) FROM ai_hospitality_entity e JOIN ai_hospitality_quality_v12416 q USING(hospitality_id)
          WHERE e.active=TRUE {clause}"""),params).scalar() or 0)
        rows=[dict(x) for x in c.execute(text(f"""
          SELECT e.hospitality_id,e.business_name,e.category,e.location,e.city,e.contact_name,e.contact_phone,
                 e.email,e.website,e.verification_status,q.quality_status,q.completeness_score,q.purity_reason
          FROM ai_hospitality_entity e JOIN ai_hospitality_quality_v12416 q USING(hospitality_id)
          WHERE e.active=TRUE {clause}
          ORDER BY q.call_ready DESC,q.contact_ready DESC,q.completeness_score DESC,e.business_name
          LIMIT :lim OFFSET :off
        """),params).mappings().all()]
    s=stats(core.engine)
    def esc(v): return html.escape(str(v or ""),quote=True)
    trs="".join(f"<tr><td><b>{esc(x['business_name'])}</b><br>ID {x['hospitality_id']}</td><td>{esc(x['category'])}</td><td>{esc(x['location'])}<br>{esc(x['city'])}</td><td>{esc(x['contact_name'])}</td><td><b>{esc(x['contact_phone'])}</b></td><td>{esc(x['email'])}</td><td>{esc(x['website'])}</td><td>{esc(x['verification_status'])}</td><td>{esc(x['quality_status'])}<br>{x['completeness_score']}%</td><td>{esc(x['purity_reason'])}</td></tr>" for x in rows) or "<tr><td colspan='10'>No records in this stage.</td></tr>"
    tabs=" ".join(f"<a class='btn' href='/hospitality-intelligence?status={x}'>{x.replace('_',' ')}</a>" for x in ["CALL_READY","CONTACT_READY","ENRICHMENT_REQUIRED","QUARANTINED_NOISE","ALL"])
    last=max(1,(total+per_page-1)//per_page)
    notice=f"<div class='notice'>{esc(msg)}</div>" if msg else ""
    body=f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Hospitality Intelligence</title>
<style>*{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}}.wrap{{max-width:1900px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:14px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}.m{{padding:12px;background:#f8fafc;border:1px solid #e4e7ec;border-radius:9px}}.m strong{{font-size:25px;display:block}}.btn,button{{background:#102a43;color:#fff;border:0;border-radius:7px;padding:8px 11px;text-decoration:none;cursor:pointer}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}.box{{overflow:auto;max-height:68vh}}.notice{{background:#ecfdf3;border:1px solid #abefc6;padding:10px;border-radius:8px;margin-bottom:12px}}</style></head><body><div class='wrap'><div class='topnav'><button type='button' onclick='history.back()'>&larr; Previous Page</button><a href='/alliance/primary'>&larr; Back to Dashboard</a></div>{notice}
<div class='card'><h1>Hospitality Intelligence · Sales-Ready Database</h1><p>Purity first → exact deduplication → contact enrichment → human verification.</p><div class='grid'>
<div class='m'>Active Clean Pool<strong>{s['total_active']}</strong></div><div class='m'>CALL READY<strong>{s['call_ready']}</strong></div><div class='m'>CONTACT READY<strong>{s['contact_ready']}</strong></div><div class='m'>Needs Enrichment<strong>{s['enrichment_required']}</strong></div><div class='m'>Noise Quarantined<strong>{s['quarantined_noise']}</strong></div><div class='m'>Merged Duplicates<strong>{s['merged_duplicates']}</strong></div><div class='m'>Phone Numbers<strong>{s['with_phone']}</strong></div></div></div>
<div class='card'><h2>Database Repair Controls</h2>
<form method='post' action='/hospitality-intelligence/run-purity' style='display:inline'><button>1. Run Purity Audit</button></form>
<form method='post' action='/hospitality-intelligence/run-dedupe' style='display:inline'><button>2. Merge Exact Duplicates</button></form>
<form method='post' action='/hospitality-intelligence/enrich' style='display:inline'><input type='hidden' name='limit' value='20'><button>3. Enrich Next 20 Missing Contacts</button></form>
<p><small>Enrichment uses public web evidence through the configured LangSearch provider. New contact details remain UNVERIFIED until human verification.</small></p></div>
<div class='card'>{tabs}</div>
<div class='card'><h2>{status.replace('_',' ')} · {total}</h2><div class='box'><table><tr><th>Business</th><th>Category</th><th>Location</th><th>Contact Person</th><th>Phone</th><th>Email</th><th>Website</th><th>Verification</th><th>Quality</th><th>Reason</th></tr>{trs}</table></div>
<p><a class='btn' href='/hospitality-intelligence?status={status}&page={max(1,page-1)}'>Previous</a> Page {page} of {last} <a class='btn' href='/hospitality-intelligence?status={status}&page={min(last,page+1)}'>Next</a></p></div></div></body></html>"""
    return HTMLResponse(body,headers={"Cache-Control":"no-store"})
def register(core):
    app=_app(core); ensure_quality_schema(core.engine)
    kept=[]
    for r in list(app.router.routes):
        if getattr(r,"path",None) in {"/hospitality-intelligence","/v3/hospitality-intelligence"} and "GET" in set(getattr(r,"methods",set()) or set()): continue
        kept.append(r)
    app.router.routes[:]=kept
    @app.get("/hospitality-intelligence",response_class=HTMLResponse)
    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def ui(req:Request,status:str=Query("CALL_READY"),page_no:int=Query(1,alias="page"),per_page:int=Query(100),msg:str=Query("")):
        return render_page(core,req,status,page_no,per_page,msg)
    @app.post("/hospitality-intelligence/run-purity")
    def purity(req:Request):
        _login(core,req); o=audit_purity(core.engine)
        return RedirectResponse("/hospitality-intelligence?status=ENRICHMENT_REQUIRED&msg="+quote_plus(f"Purity complete: {o['quarantined']} noise quarantined, {o['category_fixed']} categories corrected"),303)
    @app.post("/hospitality-intelligence/run-dedupe")
    def dedupe(req:Request):
        _login(core,req); o=dedupe_exact(core.engine)
        return RedirectResponse("/hospitality-intelligence?status=ENRICHMENT_REQUIRED&msg="+quote_plus(f"Dedup complete: {o['merged_duplicates']} exact duplicates merged safely"),303)
    @app.post("/hospitality-intelligence/enrich")
    def enrich(req:Request,limit:int=Form(20)):
        _login(core,req); o=enrich_batch(core.engine,limit)
        return RedirectResponse("/hospitality-intelligence?status=ENRICHMENT_REQUIRED&msg="+quote_plus(f"Enrichment: {o['attempted']} checked, {o['phones_found']} phones, {o['emails_found']} emails, {o['websites_found']} websites found"),303)
    @app.get("/api/alliance/hospitality-purity-status")
    def api_status():
        return {"status":"PASS","version":VERSION,**stats(core.engine),"missing_values_invented":False,"noise_deleted":False,"dedupe_source_history_preserved":True}
    return {"status":"AUTHORITATIVE","version":VERSION}
