from __future__ import annotations
import html,re,threading,urllib.parse
from pathlib import Path
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse,Response
from sqlalchemy import inspect,text
import alliance_property_brain_foundation_v1 as foundation

VERSION="1.0.1-MASTER-CONSOLIDATION"
REQ="pi_requirement_gate_v1191"; PROP="pi_master_properties_v711"
CM="pi_alliance_contact_master_v1"; CS="pi_alliance_contact_source_v1"
STATE={"status":"INIT","version":VERSION,"database_ready":False,"matcher_contract":"MASTER_ONLY","route_audit":"INIT","contact_sync":"INIT"}
PHONE=re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?([6-9]\d{9})(?!\d)")
HREF=re.compile(r'href\s*=\s*["\']([^"\'#]+)["\']',re.I)

NAV=[
("Master Properties","/alliance/final/databases"),("Availability","/alliance/primary/availability"),
("Add Property","/property-manual"),("Master Requirements","/alliance/final/requirements"),
("Add Requirement","/requirements-workbench"),("Manual Requirement DB","/alliance/final/requirements/manual"),
("WhatsApp Requirement DB","/alliance/final/requirements/whatsapp"),("Smart Matcher","/alliance/primary/matcher"),
("Automated Deal Desk","/alliance/primary/deal-desk"),("WhatsApp Live","/whatsapp-live"),
("Newspaper Capture","/capture-intelligence"),("Commercial","/commercial-intelligence"),
("Hospitality","/hospitality-intelligence"),("Retail","/retail-expansion"),
("Marketing Contacts","/alliance/primary/contact-master"),("Data Health","/alliance/primary/data-health")
]

def appof(c): return getattr(c,"app",c)
def eng(c): return foundation._engine_from_core(c)
def auth(c,r):
    try:c.need_login(r)
    except Exception as x: raise HTTPException(401,"Login required") from x
def qi(x):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(x or "")): raise ValueError("unsafe identifier")
    return '"'+x+'"'
def tabs(e):
    try:return set(inspect(e).get_table_names())
    except:return set()
def cols(e,t):
    try:return [x["name"] for x in inspect(e).get_columns(t)]
    except:return []
def cnt(e,t,where="",p=None):
    if t not in tabs(e):return 0
    with e.connect() as c:return int(c.execute(text(f"SELECT COUNT(*) FROM {qi(t)} {where}"),p or {}).scalar() or 0)
def ph(v):
    s=str(v or "").strip()
    if not s or "@lid" in s.lower():return None
    d=re.sub(r"\D","",s)
    if len(d)>=13:return None
    m=PHONE.search(s); return "+91"+m.group(1) if m else None

def req_counts(e):
    out={"ALL":0,"WHATSAPP":0,"MANUAL":0,"NEWSPAPER":0,"DISCOVERY":0,"OTHER":0}
    if REQ not in tabs(e):return out
    if "source_type" not in cols(e,REQ): out["ALL"]=cnt(e,REQ);out["OTHER"]=out["ALL"];return out
    with e.connect() as c: rows=c.execute(text(f"SELECT UPPER(COALESCE(source_type,'OTHER')),COUNT(*) FROM {REQ} GROUP BY 1")).all()
    for s,n in rows:
        s=str(s); k="WHATSAPP" if "WHATSAPP" in s else "MANUAL" if "MANUAL" in s else "NEWSPAPER" if ("NEWSPAPER" in s or "MAGAZINE" in s) else "DISCOVERY" if "DISCOVERY" in s else "OTHER"; out[k]+=int(n)
    out["ALL"]=sum(out[k] for k in ("WHATSAPP","MANUAL","NEWSPAPER","DISCOVERY","OTHER")); return out

def source_counts(e):
    ts=tabs(e)
    def first(xs):
        for x in xs:
            if x in ts:return cnt(e,x)
        return 0
    return {"WHATSAPP":first(["wa_requirements","wai_requirements"]),"MANUAL":first(["pi_manual_requirements","manual_requirements"]),"NEWSPAPER":first(["pi_newspaper_requirements","newspaper_requirements"]),"DISCOVERY":first(["pi_discovered_requirements","requirement_discovery"])}

def ensure_contacts(e):
    stmts=[
      f"CREATE TABLE IF NOT EXISTS {CM}(contact_id BIGSERIAL PRIMARY KEY,phone_e164 TEXT UNIQUE NOT NULL,display_name TEXT,contact_role TEXT DEFAULT 'UNKNOWN',source_count INTEGER DEFAULT 0,marketing_eligible BOOLEAN NOT NULL DEFAULT FALSE,do_not_contact BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW())",
      f"CREATE TABLE IF NOT EXISTS {CS}(id BIGSERIAL PRIMARY KEY,phone_e164 TEXT NOT NULL,source_table TEXT NOT NULL,source_pk TEXT NOT NULL,source_column TEXT NOT NULL,source_category TEXT NOT NULL,UNIQUE(phone_e164,source_table,source_pk,source_column))"
    ]
    with e.begin() as c:
        for s in stmts:c.execute(text(s))

def sync_contacts(e):
    STATE["contact_sync"]="RUNNING"; ensure_contacts(e); seen=0
    ts=tabs(e)
    candidates=[x for x in [PROP,REQ,"wa_properties","wa_requirements","wa_contacts","wa_messages"] if x in ts]
    for t in sorted(ts):
        if any(k in t.lower() for k in ("hospital","commercial","retail")) and t not in candidates:candidates.append(t)
    for t in candidates[:50]:
        cc=cols(e,t); pcs=[x for x in cc if x.lower() in ("phone","mobile","phone_number","contact_phone","owner_phone","broker_phone","sender_phone")]
        pk=next((x for x in ("id","canonical_id","property_id","requirement_id","wa_property_id","wa_requirement_id","message_id") if x in cc),None)
        if not pcs or not pk:continue
        names=next((x for x in ("contact_name","owner_name","broker_name","sender_name","name") if x in cc),None)
        fields=[pk]+pcs+([names] if names else [])
        with e.connect() as c: rows=c.execute(text(f"SELECT {','.join(qi(x) for x in fields)} FROM {qi(t)} LIMIT 50000")).mappings()
        batch=[]
        for r in rows:
            for pc in pcs:
                p=ph(r.get(pc))
                if not p:continue
                cat="WHATSAPP" if (t.startswith("wa_") or "whatsapp" in t.lower()) else "HOSPITALITY" if "hospital" in t.lower() else "COMMERCIAL" if "commercial" in t.lower() else "RETAIL" if "retail" in t.lower() else "MASTER"
                batch.append({"p":p,"t":t,"pk":str(r.get(pk) or ""),"col":pc,"cat":cat,"name":str(r.get(names) or "") if names else None});seen+=1
                if len(batch)>=500:
                    _ins(e,batch);batch=[]
        if batch:_ins(e,batch)
    with e.begin() as c:c.execute(text(f"UPDATE {CM} m SET source_count=s.n,updated_at=NOW() FROM (SELECT phone_e164,COUNT(*) n FROM {CS} GROUP BY 1)s WHERE m.phone_e164=s.phone_e164"))
    STATE["contact_sync"]="PASS";STATE["contact_evidence_pairs"]=seen;STATE["contact_master_count"]=cnt(e,CM)

def _ins(e,b):
    with e.begin() as c:
        c.execute(text(f"INSERT INTO {CM}(phone_e164,display_name) VALUES(:p,:name) ON CONFLICT(phone_e164) DO UPDATE SET display_name=COALESCE({CM}.display_name,EXCLUDED.display_name),updated_at=NOW()"),b)
        c.execute(text(f"INSERT INTO {CS}(phone_e164,source_table,source_pk,source_column,source_category) VALUES(:p,:t,:pk,:col,:cat) ON CONFLICT DO NOTHING"),b)

def req_contact(e,r):
    raw=r.get("contact_numbers")
    vals=raw if isinstance(raw,list) else list(raw.values()) if isinstance(raw,dict) else [raw] if raw else []
    for v in vals:
        p=ph(v)
        if p:return p,"MASTER_REQUIREMENT_CONTACT"
    spk=str(r.get("source_pk") or "")
    if not spk or "wa_requirements" not in tabs(e):return None,"PHONE_NOT_RECOVERABLE_FROM_SOURCE"
    cc=cols(e,"wa_requirements"); key=next((x for x in ("wa_requirement_id","id","requirement_id") if x in cc),None)
    if not key:return None,"PHONE_NOT_RECOVERABLE_FROM_SOURCE"
    try:
        with e.connect() as c:w=c.execute(text(f"SELECT to_jsonb(t) FROM wa_requirements t WHERE CAST({qi(key)} AS TEXT)=:x LIMIT 1"),{"x":spk}).scalar()
        if isinstance(w,dict):
            for f in ("contact_phone","phone","mobile"):
                p=ph(w.get(f))
                if p:return p,"WA_REQUIREMENT_EXPLICIT"
            mid=str(w.get("message_id") or "")
            if mid and "wa_messages" in tabs(e):
                with e.connect() as c:m=c.execute(text("SELECT to_jsonb(t) FROM wa_messages t WHERE CAST(message_id AS TEXT)=:x LIMIT 1"),{"x":mid}).scalar()
                if isinstance(m,dict):
                    p=ph(m.get("sender_phone")) or ph(m.get("sender_name"))
                    if p:return p,"EXACT_WHATSAPP_MESSAGE_SENDER"
    except:pass
    return None,"PHONE_NOT_RECOVERABLE_FROM_SOURCE"

def paths(app):return {getattr(r,"path",None) for r in app.router.routes if getattr(r,"path",None)}
def audit_links(app):
    ps=paths(app);links=set()
    for f in list(Path.cwd().glob("*.py"))+list(Path.cwd().glob("templates/**/*.html")):
        try:s=f.read_text(encoding="utf-8",errors="ignore")
        except:continue
        for h in HREF.findall(s):
            if h.startswith("/") and not h.startswith("//") and "{" not in h and "$" not in h:links.add(h.split("?",1)[0].rstrip("/") or "/")
    missing=[]
    for h in links:
        ok=h in ps
        if not ok:
            for r in app.router.routes:
                try:
                    if r.path_regex.fullmatch(h):ok=True;break
                except:pass
        if not ok:missing.append(h)
    STATE.update({"route_audit":"PASS" if not missing else "NEEDS_REVIEW","static_links_total":len(links),"static_links_missing":len(missing),"missing_links":sorted(missing)[:100]})

def esc(x):return html.escape("" if x is None else str(x))
def layout(title,body):
    n=" ".join(f"<a href='{u}'>{esc(x)}</a>" for x,u in NAV)
    return f"<meta charset='utf-8'><style>body{{font-family:Arial;margin:22px}}a{{margin-right:12px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:7px;vertical-align:top}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.card{{border:1px solid #ddd;padding:12px;border-radius:8px}}</style><nav>{n}</nav><hr><h1>{esc(title)}</h1>{body}"

def req_home(e):
    c=req_counts(e);s=source_counts(e);cards=""
    for k,l,slug in [("ALL","Master Requirements","master"),("WHATSAPP","WhatsApp Requirements","whatsapp"),("MANUAL","Manual Requirements","manual"),("NEWSPAPER","Newspaper / Magazine","newspaper"),("DISCOVERY","Discovery","discovery")]:
        extra="" if k=="ALL" else f"<div>Source inventory: {s.get(k,0):,}</div>"
        cards+=f"<div class='card'><h3><a href='/alliance/final/requirements/{slug}'>{l}</a></h3><b style='font-size:26px'>{c.get(k,0):,}</b><div>Canonical master</div>{extra}</div>"
    return layout("Alliance Master Requirement Database",f"<p>Canonical authority: <b>{REQ}</b>. Source-only rows are counted but never silently promoted.</p><div class='cards'>{cards}</div><p><a href='/requirements-workbench'>Add Requirement</a><a href='/alliance/primary/matcher'>Smart Matcher</a><a href='/alliance/primary/deal-desk'>Automated Deal Desk</a></p>")

def req_list(e,src,page):
    cc=cols(e,REQ);where=""
    if src!="ALL" and "source_type" in cc:
        term="WHATSAPP" if src=="WHATSAPP" else "MANUAL" if src=="MANUAL" else "DISCOVERY" if src=="DISCOVERY" else "NEWSPAPER"
        where=f"WHERE UPPER(COALESCE(source_type,'')) LIKE '%{term}%'"
    want=[x for x in ("id","source_type","source_pk","original_message","raw_text","requirement_text","locations","transaction_type","property_category","contact_numbers","matcher_eligible") if x in cc]
    with e.connect() as c:rows=c.execute(text(f"SELECT {','.join(qi(x) for x in want)} FROM {REQ} {where} ORDER BY id DESC LIMIT 100 OFFSET :o"),{"o":(page-1)*100}).mappings().all()
    tr=""
    for x in rows:
        r=dict(x);p,m=req_contact(e,r);rid=r.get("id");raw=r.get("original_message") or r.get("raw_text") or r.get("requirement_text") or ""
        tr+=f"<tr><td>{esc(rid)}</td><td>{esc(r.get('source_type'))}</td><td>{esc(raw)[:600]}</td><td>{esc(r.get('locations'))}</td><td>{esc(p or 'PHONE NOT RECOVERABLE FROM SOURCE')}<br>{esc(m)}</td><td><a href='/alliance/primary/deal-desk?requirement_id={urllib.parse.quote(str(rid))}'>Run Matcher</a></td></tr>"
    return layout(src.title()+" Requirements","<table><tr><th>ID</th><th>Source</th><th>Requirement</th><th>Location</th><th>Contact</th><th>Action</th></tr>"+tr+"</table>")

def contact_page(e):
    with e.connect() as c:rows=c.execute(text(f"SELECT phone_e164,display_name,source_count,marketing_eligible,do_not_contact FROM {CM} ORDER BY updated_at DESC LIMIT 500")).mappings().all()
    tr="".join(f"<tr><td>{esc(x.phone_e164)}</td><td>{esc(x.display_name)}</td><td>{x.source_count}</td><td>{x.marketing_eligible}</td><td>{x.do_not_contact}</td></tr>" for x in rows)
    return layout("Alliance Marketing Contact Master",f"<p>{cnt(e,CM):,} deduplicated contacts. Marketing eligibility defaults FALSE.</p><table><tr><th>Phone</th><th>Name</th><th>Sources</th><th>Marketing Eligible</th><th>DNC</th></tr>{tr}</table>")

def inject_dashboard(s):
    if "ALLIANCE_MASTER_CONSOLIDATION_V1" in s:return s
    cards="".join(f"<a href='{u}' style='display:block;border:1px solid #ddd;padding:9px'>{esc(n)}</a>" for n,u in NAV)
    p=f"<!-- ALLIANCE_MASTER_CONSOLIDATION_V1 --><section><h2>Alliance Master Data & Team Workflow</h2><div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px'>{cards}</div></section>"
    i=s.lower().rfind("</body>");return s[:i]+p+s[i:] if i>=0 else s+p
def clean(s):
    for a,b in {"â†’":"→","â†":"←","Â·":"·"}.items():s=s.replace(a,b)
    return s

def register(core):
    app=appof(core);e=eng(core);ps=paths(app)
    if "/api/alliance/master-consolidation-v1/public-status" not in ps:
        @app.get("/api/alliance/master-consolidation-v1/public-status")
        def pub():return {k:STATE.get(k) for k in ("status","version","database_ready","matcher_contract","route_audit","static_links_total","static_links_missing","contact_sync")}
    if "/api/alliance/master-consolidation-v1/status" not in ps:
        @app.get("/api/alliance/master-consolidation-v1/status")
        def st(req:Request):
            auth(core,req);x=dict(STATE);x["requirement_counts"]=req_counts(e);x["source_counts"]=source_counts(e);x["master_properties"]=cnt(e,PROP);x["contact_master"]=cnt(e,CM);return x
    if "/alliance/primary/contact-master" not in ps:
        @app.get("/alliance/primary/contact-master",response_class=HTMLResponse)
        def cp(req:Request):auth(core,req);return HTMLResponse(contact_page(e))
    @app.middleware("http")
    async def mw(req:Request,call_next):
        p=req.url.path
        if p=="/alliance/final/requirements" or p.startswith("/alliance/final/requirements/"):
            try:auth(core,req)
            except:return JSONResponse({"detail":"Login required"},401)
            if p=="/alliance/final/requirements":return HTMLResponse(req_home(e))
            src=p.rsplit("/",1)[-1].upper();src={"MASTER":"ALL","MAGAZINE":"NEWSPAPER"}.get(src,src)
            if src in ("ALL","WHATSAPP","MANUAL","NEWSPAPER","DISCOVERY"):
                try:page=max(1,int(req.query_params.get("page","1")))
                except:page=1
                return HTMLResponse(req_list(e,src,page))
        r=await call_next(req)
        if "text/html" not in r.headers.get("content-type",""):return r
        if not (p=="/alliance/primary" or p.startswith("/alliance/")):return r
        try:
            b=b""
            async for z in r.body_iterator:b+=z
            s=clean(b.decode("utf-8",errors="replace"))
            if p=="/alliance/primary":s=inject_dashboard(s)
            h=dict(r.headers);h.pop("content-length",None);return Response(s,r.status_code,headers=h,media_type="text/html")
        except:return r
    try:
        if e is None:raise RuntimeError("DATABASE_ENGINE_UNAVAILABLE")
        if REQ not in tabs(e) or PROP not in tabs(e):raise RuntimeError("MASTER_AUTHORITY_MISSING")
        ensure_contacts(e);STATE.update({"status":"READY","database_ready":True})
        threading.Thread(target=sync_contacts,args=(e,),daemon=True).start()
    except Exception as x:STATE.update({"status":"ERROR","startup_error":str(x)})
    audit_links(app);return dict(STATE)
