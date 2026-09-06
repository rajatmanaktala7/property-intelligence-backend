from __future__ import annotations
import hashlib, html, json, re
from fastapi import Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="11.9.1-GENUINE-REQUIREMENT-GATE"
STATUSES=("RAW","AI-QUALIFIED","NEEDS VERIFICATION","VERIFIED ACTIVE","REJECTED/EXPIRED")
DDL=[
"""CREATE TABLE IF NOT EXISTS pi_requirement_gate_v1191(
id BIGSERIAL PRIMARY KEY,evidence_key TEXT NOT NULL UNIQUE,source_type TEXT NOT NULL,source_table TEXT,source_pk TEXT,source_group TEXT,source_date TIMESTAMPTZ,
original_message TEXT NOT NULL,message_hash TEXT NOT NULL,classification TEXT NOT NULL DEFAULT 'RAW',genuine_confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
rejection_reason TEXT,transaction_type TEXT,property_category TEXT,intended_use TEXT,locations JSONB NOT NULL DEFAULT '[]'::jsonb,
alternate_locations JSONB NOT NULL DEFAULT '[]'::jsonb,area_min_sqft NUMERIC,area_max_sqft NUMERIC,budget_min NUMERIC,budget_max NUMERIC,
floor_requirement TEXT,frontage_requirement TEXT,parking_requirement TEXT,company_brand_person TEXT,contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
extracted_fields JSONB NOT NULL DEFAULT '{}'::jsonb,evidence_quality TEXT NOT NULL DEFAULT 'UNKNOWN',duplicate_of TEXT,verified_by TEXT,verified_at TIMESTAMPTZ,
verification_notes TEXT,expires_at TIMESTAMPTZ,matcher_eligible BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE INDEX IF NOT EXISTS idx_req_gate_status ON pi_requirement_gate_v1191(classification,matcher_eligible)""",
"""CREATE INDEX IF NOT EXISTS idx_req_gate_hash ON pi_requirement_gate_v1191(message_hash)""",
"""CREATE TABLE IF NOT EXISTS pi_requirement_gate_audit_v1191(id BIGSERIAL PRIMARY KEY,gate_id BIGINT,action TEXT NOT NULL,actor TEXT,old_status TEXT,new_status TEXT,details JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
]
POSITIVE=(r"\bneed(?:ed|ing)?\b",r"\brequire(?:d|ment)?\b",r"\blooking\s+for\b",r"\bwanted\b",r"\bseeking\b",r"\bclient\s+(?:needs|requires|looking)\b",r"\bspace\s+(?:needed|required)\b")
SUPPLY=(r"\bfor\s+sale\b",r"\bavailable\s+for\b",r"\bavailable\s+(?:on\s+)?rent\b",r"\bto\s+let\b",r"\bproperty\s+available\b",r"\binventory\s+available\b")
NOISE=(r"^\s*(?:hi|hello|good morning|good evening|thanks|thank you|ok|okay)\W*$",r"\bsubscribe\b",r"\bfollow\s+us\b")

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req): return getattr(core,"need_login",lambda r:"team")(req)
def _actor(core,req): return getattr(core,"actor_name",lambda r:"team")(req)
def _e(v): return html.escape("" if v is None else str(v))
def _norm(s): return re.sub(r"\s+"," ",str(s or "")).strip()
def _hash(s): return hashlib.sha256(_norm(s).lower().encode()).hexdigest()
def _phones(s):
    out=[]
    for x in re.findall(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)",s or ""):
        if x not in out: out.append(x)
    return out
def _money(n,u):
    x=float(n);u=u.lower()
    return x*10000000 if u in ("cr","crore") else x*100000 if u in ("l","lac","lakh") else x*1000 if u=="k" else x

def extract(raw):
    s=_norm(raw);up=s.upper()
    tx="RENT" if re.search(r"\b(?:rent|lease|leasing|take on rent)\b",s,re.I) else ("SALE" if re.search(r"\b(?:buy|purchase|outright|acquire)\b",s,re.I) else None)
    use=next((u for u in ("RESTAURANT","CAFE","BANQUET","OFFICE","RETAIL","SHOWROOM","WAREHOUSE","HOTEL","LOUNGE","CLUB","GUEST HOUSE","INDUSTRIAL","FARMHOUSE") if u in up),None)
    cat="COMMERCIAL" if use in ("RESTAURANT","CAFE","BANQUET","OFFICE","RETAIL","SHOWROOM","HOTEL","LOUNGE","CLUB","GUEST HOUSE") else ("INDUSTRIAL" if use in ("WAREHOUSE","INDUSTRIAL") else ("FARMHOUSE" if use=="FARMHOUSE" else None))
    amin=amax=None
    m=re.search(r"(?i)\b(\d{2,7}(?:,\d{3})*)\s*(?:-|to|–)\s*(\d{2,7}(?:,\d{3})*)\s*(?:sq\.?\s*ft|sqft|sft)\b",s)
    if m: amin,amax=sorted((float(m.group(1).replace(",","")),float(m.group(2).replace(",",""))))
    else:
        m=re.search(r"(?i)\b(\d{2,7}(?:,\d{3})*)\s*(?:sq\.?\s*ft|sqft|sft)\b",s)
        if m: amin=amax=float(m.group(1).replace(",",""))
    vals=[_money(n,u) for n,u in re.findall(r"(?i)₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh|k)\b",s)]
    bmin,bmax=((min(vals),max(vals)) if len(vals)>1 else (None,vals[0]) if vals else (None,None))
    locs=[]
    try:
        from property_brain.utils import load_json
        for a,c in load_json("location_aliases_seed.json").items():
            if str(a).upper() in up and c not in locs: locs.append(c)
    except Exception: pass
    positive=sum(bool(re.search(p,s,re.I)) for p in POSITIVE); supply=sum(bool(re.search(p,s,re.I)) for p in SUPPLY); noise=sum(bool(re.search(p,s,re.I)) for p in NOISE)
    phones=_phones(s); details=sum(bool(x) for x in (tx,use,locs,amin,bmax,phones))
    conf=max(0,min(.99,.18+positive*.24+details*.07-supply*.24-noise*.35))
    if noise or (supply and not positive): status="REJECTED/EXPIRED"; reason="noise_or_supply_side"
    elif positive and details>=2: status="AI-QUALIFIED"; reason=None
    elif positive: status="NEEDS VERIFICATION"; reason=None
    else: status="RAW"; reason=None
    return dict(classification=status,genuine_confidence=round(conf,4),rejection_reason=reason,transaction_type=tx,property_category=cat,intended_use=use,
      locations=locs,alternate_locations=[],area_min_sqft=amin,area_max_sqft=amax,budget_min=bmin,budget_max=bmax,contact_numbers=phones,
      evidence_quality="STRONG" if positive and details>=3 else "PARTIAL" if positive else "UNKNOWN")

def candidate_tables(engine):
    out=[]
    with engine.connect() as c:
        tables=c.execute(text("""SELECT table_name FROM information_schema.tables WHERE table_schema='public'
          AND (table_name ILIKE '%require%' OR table_name ILIKE '%demand%') ORDER BY table_name""")).scalars().all()
        for t in tables:
            if t in ("pi_requirement_gate_v1191","pi_requirement_gate_audit_v1191","pi_master_requirements_v711"): continue
            names=c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"),{"t":t}).scalars().all()
            tc=next((x for x in ("raw_message","original_message","message","raw_text","description","requirement","requirement_text","details","notes","text","content") if x in names),None)
            if not tc: continue
            pk=next((x for x in ("id","record_id","requirement_id","source_id") if x in names),None)
            dc=next((x for x in ("source_date","captured_at","created_at","entry_date","date_captured") if x in names),None)
            gc=next((x for x in ("source_group","group_name","source_name","source") if x in names),None)
            out.append((t,tc,pk,dc,gc))
    return out

def recover(engine,limit_per_table=5000):
    stats={"seen":0,"inserted":0,"duplicates":0,"tables":[]}
    for t,tc,pk,dc,gc in candidate_tables(engine):
        cols=[f'"{tc}"::text raw',f'"{pk}"::text pk' if pk else "NULL::text pk",f'"{dc}" dt' if dc else "NULL::timestamptz dt",f'"{gc}"::text grp' if gc else "NULL::text grp"]
        try:
            with engine.connect() as c: rows=c.execute(text(f'SELECT {",".join(cols)} FROM "{t}" WHERE "{tc}" IS NOT NULL AND LENGTH(TRIM("{tc}"::text))>=8 LIMIT :n'),{"n":limit_per_table}).mappings().all()
        except Exception as exc:
            stats["tables"].append({"table":t,"status":"SKIPPED","error":str(exc)[:120]});continue
        ins=dup=0
        for r in rows:
            raw=_norm(r["raw"]); h=_hash(raw); ev=f"{t}:{r['pk'] or h[:20]}"; ex=extract(raw);stats["seen"]+=1
            with engine.begin() as c:
                if c.execute(text("SELECT 1 FROM pi_requirement_gate_v1191 WHERE message_hash=:h LIMIT 1"),{"h":h}).scalar():
                    dup+=1;stats["duplicates"]+=1;continue
                got=c.execute(text("""INSERT INTO pi_requirement_gate_v1191(evidence_key,source_type,source_table,source_pk,source_group,source_date,original_message,message_hash,
                classification,genuine_confidence,rejection_reason,transaction_type,property_category,intended_use,locations,alternate_locations,area_min_sqft,area_max_sqft,budget_min,budget_max,
                contact_numbers,extracted_fields,evidence_quality,matcher_eligible) VALUES(:ev,:st,:tb,:pk,:grp,:dt,:raw,:h,:cl,:cf,:rr,:tx,:cat,:use,CAST(:loc AS JSONB),'[]'::jsonb,:amin,:amax,:bmin,:bmax,
                CAST(:phones AS JSONB),CAST(:fields AS JSONB),:eq,FALSE) ON CONFLICT(evidence_key) DO NOTHING RETURNING id"""),
                {"ev":ev,"st":t.upper(),"tb":t,"pk":r["pk"],"grp":r["grp"],"dt":r["dt"],"raw":raw,"h":h,"cl":ex["classification"],"cf":ex["genuine_confidence"],"rr":ex["rejection_reason"],
                 "tx":ex["transaction_type"],"cat":ex["property_category"],"use":ex["intended_use"],"loc":json.dumps(ex["locations"]),"amin":ex["area_min_sqft"],"amax":ex["area_max_sqft"],
                 "bmin":ex["budget_min"],"bmax":ex["budget_max"],"phones":json.dumps(ex["contact_numbers"]),"fields":json.dumps(ex),"eq":ex["evidence_quality"]}).scalar()
                if got:ins+=1;stats["inserted"]+=1
        stats["tables"].append({"table":t,"status":"OK","rows":len(rows),"inserted":ins,"duplicates":dup})
    return stats

def counts(engine):
    d={s:0 for s in STATUSES}
    with engine.connect() as c:
        for s,n in c.execute(text("SELECT classification,COUNT(*) FROM pi_requirement_gate_v1191 GROUP BY classification")).all(): d[s]=int(n)
        d["RAW EVIDENCE"]=int(c.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191")).scalar() or 0)
        d["MATCHER ELIGIBLE"]=int(c.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE classification='VERIFIED ACTIVE' AND matcher_eligible=TRUE")).scalar() or 0)
    return d

def shell(body):
    return """<!doctype html><html><head><meta charset=utf-8><style>body{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}header{background:#10223f;color:white;padding:16px}nav,.wrap{padding:10px}nav a,button{background:#10223f;color:white;padding:8px;text-decoration:none;border:0;margin:2px}.grid{display:grid;grid-template-columns:repeat(6,minmax(150px,1fr));gap:6px}.card{background:white;border:1px solid #98a2b3;padding:10px}.num{font-size:26px;font-weight:bold}.tablebox{overflow:auto;max-height:70vh}table{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}th,td{border:1px solid #98a2b3;padding:6px;vertical-align:top}th{background:#e9eef5;position:sticky;top:0}.msg{min-width:380px;max-width:560px}</style></head><body><header><b>Alliance Genuine Requirement Gate - CRE 11.9.1</b><br>Only VERIFIED ACTIVE can be Matcher Eligible.</header><nav><a href='/alliance/primary'>Back to Dashboard</a><a href='/alliance/requirements-gate'>Requirement Gate</a><a href='/alliance/primary/matcher'>Matcher</a></nav><div class=wrap>"""+body+"</div></body></html>"

def register(core):
    app=_app(core);engine=_engine(core)
    with engine.begin() as c:
        for ddl in DDL:c.execute(text(ddl))
    @app.get("/api/cre1191/requirements/status")
    def status(req:Request):
        _login(core,req);return {"version":VERSION,"counts":counts(engine),"candidate_tables":[x[0] for x in candidate_tables(engine)]}
    @app.post("/alliance/requirements-gate/recover")
    def do_recover(req:Request,limit_per_table:int=Form(5000)):
        _login(core,req);recover(engine,max(100,min(limit_per_table,25000)));return RedirectResponse("/alliance/requirements-gate",303)
    @app.post("/alliance/requirements-gate/{gid}/decision")
    def decision(req:Request,gid:int,status:str=Form(...),verified_by:str=Form(""),notes:str=Form("")):
        _login(core,req)
        if status not in STATUSES:raise HTTPException(400,"Invalid status")
        actor=verified_by.strip() or _actor(core,req); eligible=status=="VERIFIED ACTIVE"
        with engine.begin() as c:
            old=c.execute(text("SELECT classification FROM pi_requirement_gate_v1191 WHERE id=:id"),{"id":gid}).scalar()
            if old is None:raise HTTPException(404,"Not found")
            c.execute(text("""UPDATE pi_requirement_gate_v1191 SET classification=:s,matcher_eligible=:m,verified_by=CASE WHEN :m THEN :by ELSE verified_by END,
              verified_at=CASE WHEN :m THEN NOW() ELSE verified_at END,verification_notes=:n,updated_at=NOW() WHERE id=:id"""),{"s":status,"m":eligible,"by":actor,"n":notes,"id":gid})
            c.execute(text("""INSERT INTO pi_requirement_gate_audit_v1191(gate_id,action,actor,old_status,new_status,details) VALUES(:id,'TEAM_DECISION',:by,:o,:s,CAST(:d AS JSONB))"""),
              {"id":gid,"by":actor,"o":old,"s":status,"d":json.dumps({"notes":notes})})
        return RedirectResponse("/alliance/requirements-gate",303)
    @app.get("/alliance/requirements-gate",response_class=HTMLResponse)
    def page(req:Request,status_filter:str=Query(""),q:str=Query(""),limit:int=Query(200,ge=1,le=1000)):
        _login(core,req); cs=counts(engine); cards="".join(f"<div class=card><b>{_e(k)}</b><div class=num>{v:,}</div></div>" for k,v in cs.items())
        wh=["1=1"];p={"n":limit}
        if status_filter:wh.append("classification=:s");p["s"]=status_filter
        if q:wh.append("(original_message ILIKE :q OR COALESCE(source_group,'') ILIKE :q)");p["q"]="%"+q+"%"
        with engine.connect() as c:rows=c.execute(text("SELECT * FROM pi_requirement_gate_v1191 WHERE "+" AND ".join(wh)+" ORDER BY updated_at DESC,id DESC LIMIT :n"),p).mappings().all()
        opts="<option value=''>ALL</option>"+"".join(f"<option {'selected' if status_filter==s else ''}>{s}</option>" for s in STATUSES)
        trs=[]
        for r in rows:
            loc=", ".join(r["locations"] or []) if isinstance(r["locations"],list) else str(r["locations"] or ""); ph=", ".join(r["contact_numbers"] or []) if isinstance(r["contact_numbers"],list) else str(r["contact_numbers"] or "")
            dec="<form method=post action='/alliance/requirements-gate/%s/decision'><select name=status>%s</select><input name=verified_by placeholder='Verified By'><input name=notes placeholder='Notes'><button>Save</button></form>"%(r["id"],"".join(f"<option>{s}</option>" for s in STATUSES))
            vals=(r["id"],r["classification"],r["source_table"],r["original_message"],r["transaction_type"],r["intended_use"],loc,r["area_min_sqft"],r["area_max_sqft"],r["budget_max"],ph,"YES" if r["matcher_eligible"] else "NO")
            trs.append("<tr>"+"".join(f"<td{' class=msg' if i==3 else ''}>{_e(v)}</td>" for i,v in enumerate(vals))+f"<td>{dec}</td></tr>")
        body=f"<div class=grid>{cards}</div><div class=card><form><select name=status_filter>{opts}</select><input name=q value='{_e(q)}' placeholder='Search'><button>Search</button></form><form method=post action='/alliance/requirements-gate/recover'><input type=number name=limit_per_table value=5000><button>Recover Historical Evidence Through Gate</button></form><p><b>Safety:</b> Recovery writes only to gate staging. It does not add historical records to Master Requirements.</p></div><div class=tablebox><table><tr><th>ID</th><th>Status</th><th>Source</th><th>Original Message</th><th>Intent</th><th>Use</th><th>Location</th><th>Area Min</th><th>Area Max</th><th>Budget</th><th>Contact</th><th>Matcher</th><th>Decision</th></tr>{''.join(trs)}</table></div>"
        return HTMLResponse(shell(body),headers={"Cache-Control":"no-store","X-Alliance-CRE-Version":VERSION})
    return {"status":"REGISTERED","version":VERSION,"policy":"ONLY VERIFIED ACTIVE IS MATCHER ELIGIBLE","candidate_tables":[x[0] for x in candidate_tables(engine)],"counts":counts(engine)}
