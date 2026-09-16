from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "1.0.0-EVIDENCE-ONLY-CLEAN-CONTACT-MASTER"
MARKER = "ALLIANCE_ASTRA_CLEAN_CONTACT_MASTER_V1"
SOURCE_TOKENS = ("whatsapp", "newspaper", "magazine", "hospitality", "retail", "commercial", "manual")
PHONE_RE = re.compile(r"(?<!\\d)(?:\\+?91[\\s.-]?)?([6-9](?:[\\s.-]?\\d){9})(?!\\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}", re.I)

def _app(core): return getattr(core, "app", core)
def _engine(core): return getattr(core, "engine", None)
def _role(core, req):
    fn = getattr(core, "need_login", None)
    if callable(fn): return fn(req)
    raise HTTPException(401, "Login required")
def _qident(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")): raise ValueError("Unsafe identifier")
    return '"' + str(value) + '"'
def _walk(value, keys):
    wanted={str(k).lower() for k in keys}; out=[]
    def visit(x):
        if isinstance(x, dict):
            for k,v in x.items():
                if str(k).lower() in wanted and v not in (None,"",[],{}): out.append(v)
                visit(v)
        elif isinstance(x, list):
            for y in x: visit(y)
        elif isinstance(x, str):
            try:
                parsed=json.loads(x)
                if isinstance(parsed,(dict,list)): visit(parsed)
            except Exception: pass
    visit(value); return out
def _first(value, keys):
    for item in _walk(value, keys):
        if isinstance(item, (str,int,float)):
            clean=str(item).strip()
            if clean: return clean
    return ""
def _phones(value):
    found=[]
    for raw in value if isinstance(value,list) else [value]:
        for m in PHONE_RE.finditer(str(raw or "").replace("@s.whatsapp.net","")):
            digits=re.sub(r"\\D","",m.group(1))
            if len(digits)==10 and digits not in found: found.append(digits)
    return found[:5]
def _email(value):
    for raw in value if isinstance(value,list) else [value]:
        m=EMAIL_RE.search(str(raw or ""))
        if m: return m.group(0).lower()
    return ""
def _source_kind(table):
    low=table.lower()
    if "whatsapp" in low or low.startswith("wa_"): return "WHATSAPP"
    if "newspaper" in low: return "NEWSPAPER"
    if "magazine" in low: return "MAGAZINE"
    if "hospitality" in low: return "HOSPITALITY"
    if "retail" in low: return "RETAIL"
    if "commercial" in low: return "COMMERCIAL"
    return "MANUAL"
def _tables(engine):
    with engine.connect() as c:
        names=c.execute(text("""SELECT table_name FROM information_schema.tables
          WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name""")).scalars().all()
    return [n for n in names if any(t in n.lower() for t in SOURCE_TOKENS)
        and n not in {"pi_clean_contacts_v1","pi_clean_contact_evidence_v1"}][:120]
def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_clean_contacts_v1(
          id BIGSERIAL PRIMARY KEY, canonical_key TEXT UNIQUE NOT NULL, contact_name TEXT, company_name TEXT,
          phone TEXT, whatsapp_phone TEXT, email TEXT, designation TEXT, location TEXT,
          source_count INTEGER NOT NULL DEFAULT 0, first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          marketing_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_clean_contact_evidence_v1(
          id BIGSERIAL PRIMARY KEY, canonical_key TEXT NOT NULL REFERENCES pi_clean_contacts_v1(canonical_key) ON DELETE CASCADE,
          source_type TEXT NOT NULL, source_table TEXT NOT NULL, source_record_id TEXT NOT NULL,
          source_captured_at TEXT, evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE(source_table,source_record_id,canonical_key))"""))
def sync(engine, limit_per_table=3000):
    ensure_schema(engine); stats={"tables":0,"rows":0,"contacts_upserted":0,"evidence_upserted":0}
    for table in _tables(engine):
        try:
            with engine.connect() as c:
                rows=c.execute(text(f"SELECT to_jsonb(t) AS data FROM {_qident(table)} t LIMIT :n"),{"n":int(limit_per_table)}).scalars().all()
        except Exception: continue
        stats["tables"]+=1
        for pos, raw in enumerate(rows,1):
            obj=raw if isinstance(raw,dict) else json.loads(raw) if isinstance(raw,str) else {}
            if not isinstance(obj,dict): continue
            phones=_phones(_walk(obj,["phone","phones","mobile","mobile_no","contact_no","contact_number","contact_phone","whatsapp_phone","sender_phone","sender_mobile","sender_jid","remote_jid","from"]))
            email=_email(_walk(obj,["email","contact_email","email_id"]))
            if not phones and not email: continue
            stats["rows"]+=1
            name=_first(obj,["contact_name","sender_name","client_name","owner_name","broker_name","name","person_name"])
            company=_first(obj,["company_name","brand_name","business_name","agency_brand","retailer_name","company"])
            location=_first(obj,["location","locality","city","area_name","micro_market"])
            designation=_first(obj,["designation","role","title"])
            source_id=_first(obj,["id","record_id","event_id","message_id","source_id","requirement_id"]) or str(pos)
            captured=_first(obj,["captured_at","created_at","message_timestamp","timestamp","date"])
            for phone in phones or [""]:
                keyseed="|".join([phone,email.lower(),re.sub(r"\\s+"," ",name.lower()),re.sub(r"\\s+"," ",company.lower())])
                key="CONTACT-"+hashlib.sha256(keyseed.encode("utf-8","ignore")).hexdigest()[:24].upper()
                with engine.begin() as c:
                    c.execute(text("""INSERT INTO pi_clean_contacts_v1(canonical_key,contact_name,company_name,phone,whatsapp_phone,email,designation,location,source_count)
                      VALUES(:k,:n,:co,:p,:wp,:e,:d,:l,1)
                      ON CONFLICT(canonical_key) DO UPDATE SET
                      contact_name=COALESCE(NULLIF(EXCLUDED.contact_name,''),pi_clean_contacts_v1.contact_name),
                      company_name=COALESCE(NULLIF(EXCLUDED.company_name,''),pi_clean_contacts_v1.company_name),
                      phone=COALESCE(NULLIF(EXCLUDED.phone,''),pi_clean_contacts_v1.phone),
                      whatsapp_phone=COALESCE(NULLIF(EXCLUDED.whatsapp_phone,''),pi_clean_contacts_v1.whatsapp_phone),
                      email=COALESCE(NULLIF(EXCLUDED.email,''),pi_clean_contacts_v1.email),
                      designation=COALESCE(NULLIF(EXCLUDED.designation,''),pi_clean_contacts_v1.designation),
                      location=COALESCE(NULLIF(EXCLUDED.location,''),pi_clean_contacts_v1.location),
                      last_seen_at=NOW(),updated_at=NOW()"""),{"k":key,"n":name,"co":company,"p":phone,"wp":phone if _source_kind(table)=="WHATSAPP" else "","e":email,"d":designation,"l":location})
                    result=c.execute(text("""INSERT INTO pi_clean_contact_evidence_v1(canonical_key,source_type,source_table,source_record_id,source_captured_at,evidence_json)
                      VALUES(:k,:st,:tb,:sid,:at,CAST(:ev AS JSONB)) ON CONFLICT(source_table,source_record_id,canonical_key) DO NOTHING"""),
                      {"k":key,"st":_source_kind(table),"tb":table,"sid":source_id,"at":captured,"ev":json.dumps({"name":name,"company":company,"phone":phone,"email":email,"location":location},ensure_ascii=False)})
                    stats["contacts_upserted"]+=1; stats["evidence_upserted"]+=max(0,result.rowcount or 0)
    with engine.begin() as c:
        c.execute(text("""UPDATE pi_clean_contacts_v1 x SET source_count=s.n FROM
          (SELECT canonical_key,COUNT(*) n FROM pi_clean_contact_evidence_v1 GROUP BY canonical_key) s
          WHERE x.canonical_key=s.canonical_key"""))
    return stats
def register(core):
    app=_app(core); engine=_engine(core)
    if engine is None: raise RuntimeError("Contact master requires database engine")
    @app.get("/api/alliance/clean-contact-master-v1/status",include_in_schema=False)
    def status(req:Request):
        _role(core,req); ensure_schema(engine)
        with engine.connect() as c: total=c.execute(text("SELECT COUNT(*) FROM pi_clean_contacts_v1")).scalar() or 0
        return {"status":"READY","version":VERSION,"contacts":total,"policy":"EVIDENCE_ONLY_REVIEW_REQUIRED","gpt_used":False}
    @app.post("/api/alliance/clean-contact-master-v1/sync",include_in_schema=False)
    def run_sync(req:Request):
        _role(core,req); return {"status":"APPLIED","version":VERSION,**sync(engine)}
    @app.get("/alliance/marketing-contacts",response_class=HTMLResponse,include_in_schema=False)
    def page(req:Request,limit:int=200):
        _role(core,req); ensure_schema(engine)
        with engine.connect() as c:
            rows=c.execute(text("""SELECT contact_name,company_name,phone,whatsapp_phone,email,designation,location,source_count,verification_status,marketing_status,last_seen_at
              FROM pi_clean_contacts_v1 ORDER BY last_seen_at DESC LIMIT :n"""),{"n":max(1,min(int(limit),1000))}).mappings().all()
        body="".join("<tr>"+ "".join(f"<td>{html.escape(str(r.get(k) or ''))}</td>" for k in ["contact_name","company_name","phone","whatsapp_phone","email","designation","location","source_count","verification_status","marketing_status","last_seen_at"])+"</tr>" for r in rows)
        return HTMLResponse(f"""<!doctype html><meta charset=utf-8><title>Marketing Contacts</title><style>body{{font:14px Arial;margin:24px;color:#162235}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #dce3ec;padding:8px;text-align:left}}th{{background:#10223f;color:#fff}}button{{padding:9px 12px;background:#1d4ed8;color:#fff;border:0;border-radius:6px}}</style><h1>Marketing Contacts</h1><p>Evidence-backed contacts only. Marketing status defaults to Review Required.</p><button onclick="fetch('/api/alliance/clean-contact-master-v1/sync',{{method:'POST'}}).then(r=>r.json()).then(x=>location.reload())">Sync all sources</button><table><tr><th>Name</th><th>Company</th><th>Phone</th><th>WhatsApp</th><th>Email</th><th>Designation</th><th>Location</th><th>Sources</th><th>Verification</th><th>Marketing status</th><th>Last seen</th></tr>{body}</table>""")
    return {"status":"REGISTERED","version":VERSION,"routes":["/alliance/marketing-contacts","/api/alliance/clean-contact-master-v1/status"]}
