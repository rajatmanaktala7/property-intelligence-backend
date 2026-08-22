
import os, re, json, uuid, hashlib
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, text

from whatsapp_intelligence import _db_url, match_score, esc, money

router = APIRouter(prefix="/whatsapp-automation", tags=["WhatsApp Automation"])

WA_DATABASE_URL=os.getenv("WHATSAPP_DATABASE_URL","").strip()
wa_engine=create_engine(_db_url(WA_DATABASE_URL),pool_pre_ping=True,pool_recycle=300) if WA_DATABASE_URL else None
HOT_MATCH_SCORE=int(os.getenv("WA_HOT_MATCH_SCORE","80"))
AUTO_MATCH_SCORE=int(os.getenv("WA_AUTO_MATCH_SCORE","70"))

SCHEMA="""
CREATE TABLE IF NOT EXISTS wa_hot_leads(
 id BIGSERIAL PRIMARY KEY,
 hot_lead_id TEXT UNIQUE NOT NULL,
 wa_requirement_id TEXT NOT NULL,
 wa_property_id TEXT NOT NULL,
 match_score NUMERIC(5,2) NOT NULL,
 grade TEXT,
 priority TEXT DEFAULT 'NORMAL',
 reasons JSONB DEFAULT '[]'::jsonb,
 status TEXT DEFAULT 'HOT_LEAD',
 team_action TEXT DEFAULT 'REVIEW_REQUIRED',
 requirement_posted_at TEXT,
 property_posted_at TEXT,
 last_matched_at TIMESTAMPTZ DEFAULT NOW(),
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(wa_requirement_id,wa_property_id)
);
CREATE TABLE IF NOT EXISTS wa_outbound_drafts(
 id BIGSERIAL PRIMARY KEY,
 draft_id TEXT UNIQUE NOT NULL,
 hot_lead_id TEXT NOT NULL,
 recipient_phone TEXT,
 recipient_name TEXT,
 message_text TEXT NOT NULL,
 channel TEXT DEFAULT 'WHATSAPP',
 status TEXT DEFAULT 'READY_FOR_REVIEW',
 approved_by TEXT,
 approved_at TIMESTAMPTZ,
 sent_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wa_hot_leads_score ON wa_hot_leads(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_wa_hot_leads_status ON wa_hot_leads(status);
CREATE INDEX IF NOT EXISTS idx_wa_drafts_status ON wa_outbound_drafts(status);
"""

def init_db():
    if wa_engine is None:
        raise HTTPException(503,"WHATSAPP_DATABASE_URL is not configured")
    with wa_engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def _priority(score):
    if score>=90:return "CRITICAL"
    if score>=85:return "HIGH"
    if score>=80:return "HOT"
    return "REVIEW"

def _draft_text(req,prop,score):
    loc=req.get("preferred_locations") or prop.get("location") or "your required location"
    ptype=req.get("property_type") or prop.get("property_type") or "property"
    area=prop.get("area_sqft")
    price=prop.get("rent_inr") if req.get("transaction_type")=="RENT" else prop.get("sale_price_inr")
    bits=[f"We found a {score:.0f}% match for your {ptype} requirement in {loc}."]
    if area: bits.append(f"Area: {float(area):,.0f} sq ft.")
    if price: bits.append(f"Price/Rent: {money(price)}.")
    bits.append("Our team will verify availability before sharing final details. Please confirm if you want us to proceed.")
    return " ".join(bits)

def _create_hot(c,req,prop,score,grade,reasons):
    hid="WAH-"+hashlib.sha256(f"{req['wa_requirement_id']}|{prop['wa_property_id']}".encode()).hexdigest()[:12].upper()
    c.execute(text("""INSERT INTO wa_hot_leads(
      hot_lead_id,wa_requirement_id,wa_property_id,match_score,grade,priority,reasons,status,team_action,
      requirement_posted_at,property_posted_at,last_matched_at,updated_at)
      VALUES(:h,:r,:p,:s,:g,:pri,CAST(:rs AS JSONB),'HOT_LEAD','REVIEW_REQUIRED',:rt,:pt,NOW(),NOW())
      ON CONFLICT(wa_requirement_id,wa_property_id) DO UPDATE SET
      match_score=EXCLUDED.match_score,grade=EXCLUDED.grade,priority=EXCLUDED.priority,reasons=EXCLUDED.reasons,
      last_matched_at=NOW(),updated_at=NOW()"""),{
        "h":hid,"r":req["wa_requirement_id"],"p":prop["wa_property_id"],"s":score,"g":grade,
        "pri":_priority(float(score)),"rs":json.dumps(reasons),
        "rt":str(req.get("created_at") or ""),"pt":str(prop.get("first_seen") or prop.get("created_at") or "")
    })
    recipient=req.get("contact_phone")
    if recipient:
        did="WAD-"+hashlib.sha256(f"{hid}|{recipient}".encode()).hexdigest()[:12].upper()
        c.execute(text("""INSERT INTO wa_outbound_drafts(
          draft_id,hot_lead_id,recipient_phone,recipient_name,message_text,status)
          VALUES(:d,:h,:ph,:nm,:m,'READY_FOR_REVIEW')
          ON CONFLICT(draft_id) DO UPDATE SET message_text=EXCLUDED.message_text,updated_at=NOW()"""),{
            "d":did,"h":hid,"ph":recipient,"nm":req.get("contact_name"),
            "m":_draft_text(req,prop,float(score))
        })

def _match_requirement(c,req):
    props=c.execute(text("""SELECT * FROM wa_properties
      WHERE duplicate_status<>'DUPLICATE'
      AND COALESCE(record_status,'ACTIVE')='ACTIVE'
      AND verification_status<>'VERIFIED_UNAVAILABLE'
      ORDER BY id DESC LIMIT 3000""")).mappings().all()
    count=0
    for prop in props:
        score,grade,reasons=match_score(req,prop)
        if score>=AUTO_MATCH_SCORE:
            c.execute(text("""INSERT INTO wa_matches(wa_requirement_id,wa_property_id,score,grade,reasons)
            VALUES(:r,:p,:s,:g,CAST(:rs AS JSONB))
            ON CONFLICT(wa_requirement_id,wa_property_id)
            DO UPDATE SET score=EXCLUDED.score,grade=EXCLUDED.grade,reasons=EXCLUDED.reasons,created_at=NOW()"""),{
                "r":req["wa_requirement_id"],"p":prop["wa_property_id"],"s":score,"g":grade,"rs":json.dumps(reasons)
            })
        if score>=HOT_MATCH_SCORE:
            _create_hot(c,req,prop,score,grade,reasons)
            count+=1
    return count

@router.on_event("startup")
def startup():
    if wa_engine is not None:
        try:init_db()
        except Exception as e: print("WhatsApp Hot Lead init warning:",e)

@router.post("/api/rebuild-hot-leads")
def rebuild_hot_leads():
    init_db();total=0
    with wa_engine.begin() as c:
        reqs=c.execute(text("SELECT * FROM wa_requirements WHERE status='ACTIVE' ORDER BY id DESC LIMIT 5000")).mappings().all()
        for req in reqs:
            total+=_match_requirement(c,req)
    return {"status":"ok","hot_matches_processed":total,"threshold":HOT_MATCH_SCORE}

@router.post("/api/draft/{draft_id}/approve")
def approve_draft(draft_id:str):
    init_db()
    with wa_engine.begin() as c:
        c.execute(text("""UPDATE wa_outbound_drafts
        SET status='APPROVED_TO_SEND',approved_at=NOW(),updated_at=NOW()
        WHERE draft_id=:d"""),{"d":draft_id})
    return RedirectResponse("/whatsapp-automation",303)

@router.post("/api/hot-lead/{hot_lead_id}/status/{new_status}")
def update_status(hot_lead_id:str,new_status:str):
    init_db()
    allowed={"contacted":"CONTACTED","hold":"HOLD","not-relevant":"NOT_RELEVANT","closed":"CLOSED"}
    if new_status not in allowed: raise HTTPException(400,"Invalid status")
    with wa_engine.begin() as c:
        c.execute(text("UPDATE wa_hot_leads SET status=:s,team_action=:s,updated_at=NOW() WHERE hot_lead_id=:h"),
                  {"s":allowed[new_status],"h":hot_lead_id})
    return RedirectResponse("/whatsapp-automation",303)

@router.get("",response_class=HTMLResponse)
def dashboard():
    init_db()
    with wa_engine.begin() as c:
        stats={
          "hot":c.execute(text("SELECT COUNT(*) FROM wa_hot_leads WHERE status='HOT_LEAD'")).scalar() or 0,
          "critical":c.execute(text("SELECT COUNT(*) FROM wa_hot_leads WHERE status='HOT_LEAD' AND match_score>=90")).scalar() or 0,
          "drafts":c.execute(text("SELECT COUNT(*) FROM wa_outbound_drafts WHERE status='READY_FOR_REVIEW'")).scalar() or 0,
          "approved":c.execute(text("SELECT COUNT(*) FROM wa_outbound_drafts WHERE status='APPROVED_TO_SEND'")).scalar() or 0,
        }
        rows=c.execute(text("""SELECT h.*,r.raw_text requirement_text,r.preferred_locations,
        r.property_type requirement_type,r.contact_name,r.contact_phone,
        p.raw_text property_text,p.location,p.property_type,p.area_sqft,p.rent_inr,p.sale_price_inr,
        p.broker_name,p.broker_phone,d.draft_id,d.message_text,d.status draft_status
        FROM wa_hot_leads h
        JOIN wa_requirements r ON r.wa_requirement_id=h.wa_requirement_id
        JOIN wa_properties p ON p.wa_property_id=h.wa_property_id
        LEFT JOIN wa_outbound_drafts d ON d.hot_lead_id=h.hot_lead_id
        WHERE h.status NOT IN ('NOT_RELEVANT','CLOSED')
        ORDER BY h.match_score DESC,h.last_matched_at DESC LIMIT 100""")).mappings().all()
    cards=[]
    for r in rows:
        price=r["rent_inr"] or r["sale_price_inr"]
        draft=""
        if r["draft_id"]:
            draft=f"""<div class='draft'><b>AI WHATSAPP DRAFT · REVIEW REQUIRED</b><p>{esc(r['message_text'])}</p>
            <form method='post' action='/whatsapp-automation/api/draft/{esc(r['draft_id'])}/approve'><button>Approve to Send</button></form></div>"""
        cards.append(f"""<section class='hot-card'><div class='score'>{esc(r['match_score'])}%<small>{esc(r['grade'])}</small></div><div>
        <b class='priority'>{esc(r['priority'])}</b><div class='muted'>{esc(r['hot_lead_id'])}</div>
        <h3>{esc(r['preferred_locations'])} · {esc(r['requirement_type'])}</h3>
        <div class='two'><div><span>REQUIREMENT</span><pre>{esc(r['requirement_text'])}</pre><b>Contact:</b> {esc(r['contact_name'])} · {esc(r['contact_phone'])}</div>
        <div><span>MATCHED PROPERTY</span><pre>{esc(r['property_text'])}</pre><b>{esc(r['location'])}</b> · {esc(r['area_sqft'])} sqft · {money(price)}<br><b>Broker:</b> {esc(r['broker_name'])} · {esc(r['broker_phone'])}</div></div>
        <div class='actions'><form method='post' action='/whatsapp-automation/api/hot-lead/{esc(r['hot_lead_id'])}/status/contacted'><button>Mark Contacted</button></form>
        <form method='post' action='/whatsapp-automation/api/hot-lead/{esc(r['hot_lead_id'])}/status/hold'><button class='gray'>Hold</button></form>
        <form method='post' action='/whatsapp-automation/api/hot-lead/{esc(r['hot_lead_id'])}/status/not-relevant'><button class='gray'>Not Relevant</button></form></div>{draft}</div></section>""")
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>WhatsApp Hot Leads</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f8;font-family:Arial;color:#101828}}
    header{{background:#101828;color:white;padding:18px 24px}}main{{max-width:1500px;margin:auto;padding:18px}}
    .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.kpi,.hot-card{{background:white;border:1px solid #e4e7ec;border-radius:12px;padding:15px;margin-bottom:12px}}
    .kpi b{{font-size:28px;display:block}}.hot-card{{display:grid;grid-template-columns:100px 1fr;gap:14px}}.score{{font-size:30px;font-weight:800}}.score small{{display:block;font-size:11px}}
    .two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}pre{{white-space:pre-wrap;background:#fff8db;padding:10px;border-radius:8px}}
    span,.muted{{font-size:11px;color:#667085}}button,.btn{{border:0;background:#039855;color:white;padding:9px 12px;border-radius:7px;cursor:pointer;text-decoration:none}}
    .gray{{background:#e4e7ec;color:#101828}}.actions{{display:flex;gap:8px;margin-top:10px}}.actions form{{margin:0}}.draft{{margin-top:12px;background:#eef4ff;padding:12px;border-radius:9px}}
    .priority{{color:#b42318}}@media(max-width:800px){{.kpis{{grid-template-columns:1fr 1fr}}.hot-card{{grid-template-columns:1fr}}.two{{grid-template-columns:1fr}}}}</style></head>
    <body><header><h2 style='margin:0'>WhatsApp Hot Lead Automation</h2><small>Automatic matching → Hot Lead → AI draft → Team review. No automatic outbound sending.</small></header><main>
    <p><a class='btn gray' href='/whatsapp-intelligence/requirements'>Requirements</a> <a class='btn gray' href='/whatsapp-intelligence/properties'>Properties</a>
    <form style='display:inline' method='post' action='/whatsapp-automation/api/rebuild-hot-leads'><button>Rebuild Hot Leads</button></form></p>
    <div class='kpis'><div class='kpi'>Hot Leads<b>{stats['hot']}</b></div><div class='kpi'>Critical 90%+<b>{stats['critical']}</b></div>
    <div class='kpi'>Drafts To Review<b>{stats['drafts']}</b></div><div class='kpi'>Approved To Send<b>{stats['approved']}</b></div></div>
    <h2>Hot Lead Queue</h2>{''.join(cards) if cards else "<div class='kpi'>No hot leads yet. Click Rebuild Hot Leads.</div>"}</main></body></html>""")

@router.get("/health")
def health():
    init_db()
    with wa_engine.begin() as c:c.execute(text("SELECT 1"))
    return {"ok":True,"hot_match_score":HOT_MATCH_SCORE,"outbound_mode":"TEAM_REVIEW_REQUIRED"}
