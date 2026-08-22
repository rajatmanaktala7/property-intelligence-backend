
import os, json, uuid, hashlib, re
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Request, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import create_engine, text

from whatsapp_intelligence import (
    _db_url, esc, money, is_noise, classify, split_inventory, extract_broker_identity,
    deterministic_extract, enrich_missing, ai_enrich, confidence, fingerprint,
    duplicate_candidate, upsert_contact, match_score
)

router = APIRouter(prefix="/whatsapp-live", tags=["WhatsApp Live Bridge"])

WA_DATABASE_URL=os.getenv("WHATSAPP_DATABASE_URL","").strip()
BRIDGE_TOKEN=os.getenv("WA_BRIDGE_TOKEN","").strip()
AUTO_AI=os.getenv("WA_LIVE_AI","true").lower() in {"1","true","yes","on"}
wa_engine=create_engine(_db_url(WA_DATABASE_URL),pool_pre_ping=True,pool_recycle=300) if WA_DATABASE_URL else None

SCHEMA="""
CREATE TABLE IF NOT EXISTS wa_bridge_accounts(
 id BIGSERIAL PRIMARY KEY,
 account_id TEXT UNIQUE NOT NULL,
 label TEXT NOT NULL,
 phone TEXT UNIQUE NOT NULL,
 active BOOLEAN DEFAULT TRUE,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS wa_bridge_groups(
 id BIGSERIAL PRIMARY KEY,
 group_id TEXT UNIQUE NOT NULL,
 account_id TEXT NOT NULL,
 group_name TEXT NOT NULL,
 active BOOLEAN DEFAULT TRUE,
 auto_process BOOLEAN DEFAULT TRUE,
 source_id UUID UNIQUE NOT NULL,
 messages_received INTEGER DEFAULT 0,
 properties_found INTEGER DEFAULT 0,
 requirements_found INTEGER DEFAULT 0,
 rejected_found INTEGER DEFAULT 0,
 last_message_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(account_id,group_name)
);
CREATE TABLE IF NOT EXISTS wa_bridge_events(
 id BIGSERIAL PRIMARY KEY,
 event_id TEXT UNIQUE NOT NULL,
 group_id TEXT NOT NULL,
 external_message_id TEXT,
 sender_name TEXT,
 sender_phone TEXT,
 message_timestamp TEXT,
 raw_text TEXT NOT NULL,
 classification TEXT,
 entity_id TEXT,
 status TEXT DEFAULT 'RECEIVED',
 error_message TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 processed_at TIMESTAMPTZ,
 UNIQUE(group_id,external_message_id)
);
CREATE INDEX IF NOT EXISTS idx_wa_bridge_events_created ON wa_bridge_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wa_bridge_groups_account ON wa_bridge_groups(account_id);
"""

def init_db():
    if wa_engine is None:
        raise HTTPException(503,"WHATSAPP_DATABASE_URL is not configured")
    with wa_engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def _auth(authorization: Optional[str], x_bridge_token: Optional[str]):
    if not BRIDGE_TOKEN:
        raise HTTPException(503,"WA_BRIDGE_TOKEN is not configured")
    supplied=(x_bridge_token or "").strip()
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied=authorization[7:].strip()
    if supplied != BRIDGE_TOKEN:
        raise HTTPException(401,"Invalid bridge token")

def _account_id(phone):
    return "WAA-"+hashlib.sha256(str(phone).encode()).hexdigest()[:12].upper()

def _group_id(account_id,group_name):
    return "WAG-"+hashlib.sha256(f"{account_id}|{group_name}".encode()).hexdigest()[:12].upper()

def _source_uuid(group_id):
    return uuid.uuid5(uuid.NAMESPACE_URL,f"whatsapp-live:{group_id}")

def _ensure_source(c,source_id,group_name):
    exists=c.execute(text("SELECT 1 FROM wa_sources WHERE source_id=:s"),{"s":source_id}).first()
    if not exists:
        c.execute(text("""INSERT INTO wa_sources(source_id,source_name,group_name,ingestion_status,total_messages)
        VALUES(:s,:n,:n,'LIVE',0)"""),{"s":source_id,"n":group_name})

def _match_new_requirement(c,req):
    try:
        from whatsapp_hot_lead_engine import _match_requirement
        return _match_requirement(c,req)
    except Exception as e:
        print("live requirement hot-match warning:",repr(e))
        return 0

def _match_new_property(c,prop):
    hot=0
    reqs=c.execute(text("""SELECT * FROM wa_requirements
      WHERE status='ACTIVE' ORDER BY id DESC LIMIT 800""")).mappings().all()
    for req in reqs:
        score,grade,reasons=match_score(req,prop)
        if score>=70:
            c.execute(text("""INSERT INTO wa_matches(wa_requirement_id,wa_property_id,score,grade,reasons)
            VALUES(:r,:p,:s,:g,CAST(:rs AS JSONB))
            ON CONFLICT(wa_requirement_id,wa_property_id) DO UPDATE SET
            score=EXCLUDED.score,grade=EXCLUDED.grade,reasons=EXCLUDED.reasons,created_at=NOW()"""),{
                "r":req["wa_requirement_id"],"p":prop["wa_property_id"],"s":score,"g":grade,"rs":json.dumps(reasons)
            })
        if score>=80:
            try:
                from whatsapp_hot_lead_engine import _create_hot
                _create_hot(c,req,prop,score,grade,reasons)
                hot+=1
            except Exception as e:
                print("live property hot lead warning:",repr(e))
    return hot

def _process(c,group,ev):
    raw=(ev.get("text") or "").strip()
    sender=ev.get("sender_name") or ev.get("sender_phone") or "Unknown"
    ts=ev.get("timestamp") or datetime.now(timezone.utc).isoformat()
    source_id=group["source_id"]
    _ensure_source(c,source_id,group["group_name"])
    mid=uuid.uuid4()
    noise,reason=is_noise(raw)
    if noise:
        c.execute(text("""INSERT INTO wa_messages(message_id,source_id,message_timestamp,sender_name,sender_phone,raw_text,classification,confidence,rejection_reason)
        VALUES(:mid,:sid,:ts,:sn,:sp,:raw,'REJECTED',99,:reason)"""),{
            "mid":mid,"sid":source_id,"ts":ts,"sn":sender,"sp":ev.get("sender_phone"),"raw":raw,"reason":reason})
        c.execute(text("""INSERT INTO wa_rejected(message_id,source_id,rejection_reason,raw_text)
        VALUES(:mid,:sid,:r,:raw)"""),{"mid":mid,"sid":source_id,"r":reason,"raw":raw})
        return "REJECTED",None

    kind,base=classify(raw)
    c.execute(text("""INSERT INTO wa_messages(message_id,source_id,message_timestamp,sender_name,sender_phone,raw_text,classification,confidence)
    VALUES(:mid,:sid,:ts,:sn,:sp,:raw,:k,:conf)"""),{
        "mid":mid,"sid":source_id,"ts":ts,"sn":sender,"sp":ev.get("sender_phone"),"raw":raw,
        "k":kind,"conf":round(base*100,2)})

    if kind=="NEEDS_REVIEW":
        c.execute(text("""INSERT INTO wa_review_queue(message_id,source_id,review_reason,confidence)
        VALUES(:mid,:sid,'Live message ambiguous',:conf)"""),{"mid":mid,"sid":source_id,"conf":round(base*100,2)})
        return "REVIEW",None

    if kind=="PROPERTY_CONTACT":
        ph=ev.get("sender_phone")
        if ph:
            upsert_contact(c,sender,ph,"UNKNOWN",ts,group["group_name"],None,None,True)
        return "CONTACT",None

    parts=split_inventory(raw) if kind=="PROPERTY_INVENTORY" else [raw]
    parent_broker_name,parent_broker_phone=extract_broker_identity(raw,sender)
    created=[]
    for item_no,part in enumerate(parts,start=1):
        data=deterministic_extract(part,kind,sender)
        if AUTO_AI:
            needs_ai = (
                kind=="PROPERTY_INVENTORY" and sum(bool(data.get(k) not in (None,"","UNKNOWN")) for k in ("location","property_type","area_sqft","rent_inr","sale_price_inr"))<3
            ) or (
                kind=="PROPERTY_REQUIREMENT" and sum(bool(data.get(k) not in (None,"","UNKNOWN")) for k in ("preferred_locations","property_type","minimum_area_sqft","budget_max_inr"))<2
            )
            if needs_ai:
                data=enrich_missing(data,ai_enrich(part,kind))
        conf=confidence(data,base)
        fp=fingerprint(data,kind)

        if kind=="PROPERTY_INVENTORY":
            data["sender_name"]=parent_broker_name or data.get("sender_name")
            data["sender_phone"]=parent_broker_phone or data.get("sender_phone")
            if not data.get("broker_name"): data["broker_name"]=parent_broker_name
            if not data.get("broker_phone"): data["broker_phone"]=parent_broker_phone
            best,dupof=duplicate_candidate(c,data)
            if best>=.88 and dupof:
                c.execute(text("""UPDATE wa_properties SET last_seen=:seen,updated_at=NOW()
                WHERE wa_property_id=:p"""),{"seen":ts,"p":dupof})
                created.append(dupof)
                continue
            pid="WAP-"+uuid.uuid4().hex[:10].upper()
            ds="POSSIBLE_DUPLICATE" if best>=.70 else "UNIQUE"
            c.execute(text("""INSERT INTO wa_properties(
            wa_property_id,source_id,message_id,source_item_no,parent_message_text,record_status,fingerprint,
            property_type,transaction_type,city,location,locality,address,landmark,area_sqft,available_area_sqft,
            floor,frontage,rent_inr,sale_price_inr,cam_inr,possession,parking,suitable_for,nearby_brands,
            availability,broker_name,broker_phone,owner_name,owner_phone,sender_name,sender_phone,
            duplicate_status,duplicate_of,confidence,raw_text,first_seen,last_seen)
            VALUES(:pid,:sid,:mid,:item_no,:parent_raw,'ACTIVE',:fp,:property_type,:transaction_type,:city,:location,:locality,:address,:landmark,
            :area_sqft,:available_area_sqft,:floor,:frontage,:rent_inr,:sale_price_inr,:cam_inr,:possession,:parking,:suitable_for,:nearby_brands,
            :availability,:broker_name,:broker_phone,:owner_name,:owner_phone,:sender_name,:sender_phone,:ds,:dupof,:conf,:raw,:seen,:seen)"""),
            dict(data,pid=pid,sid=source_id,mid=mid,item_no=item_no,parent_raw=raw,fp=fp,ds=ds,dupof=dupof,conf=conf,raw=part,seen=ts))
            ph=data.get("owner_phone") or data.get("broker_phone") or data.get("sender_phone") or ev.get("sender_phone")
            nm=data.get("owner_name") or data.get("broker_name") or data.get("sender_name") or sender
            ct="OWNER" if data.get("owner_phone") else "BROKER" if data.get("broker_phone") else "UNKNOWN"
            if ph: upsert_contact(c,nm,ph,ct,ts,group["group_name"],data.get("location"),data.get("property_type"),True)
            created.append(pid)
            prop=c.execute(text("SELECT * FROM wa_properties WHERE wa_property_id=:p"),{"p":pid}).mappings().first()
            _match_new_property(c,prop)
        else:
            rid="WAR-"+uuid.uuid4().hex[:10].upper()
            c.execute(text("""INSERT INTO wa_requirements(
            wa_requirement_id,source_id,message_id,fingerprint,client_name,company_name,property_type,transaction_type,city,
            preferred_locations,minimum_area_sqft,maximum_area_sqft,budget_min_inr,budget_max_inr,floor_preference,
            frontage_requirement,suitable_category,contact_name,contact_phone,contact_type,confidence,raw_text)
            VALUES(:rid,:sid,:mid,:fp,:client_name,:company_name,:property_type,:transaction_type,:city,:preferred_locations,
            :minimum_area_sqft,:maximum_area_sqft,:budget_min_inr,:budget_max_inr,:floor_preference,:frontage_requirement,
            :suitable_category,:contact_name,:contact_phone,:contact_type,:conf,:raw)"""),
            dict(data,rid=rid,sid=source_id,mid=mid,fp=fp,conf=conf,raw=part))
            if data.get("contact_phone"):
                upsert_contact(c,data.get("contact_name"),data.get("contact_phone"),data.get("contact_type"),ts,group["group_name"],data.get("preferred_locations"),data.get("property_type"),False)
            created.append(rid)
            req=c.execute(text("SELECT * FROM wa_requirements WHERE wa_requirement_id=:r"),{"r":rid}).mappings().first()
            _match_new_requirement(c,req)
    return kind,created[0] if created else None

@router.on_event("startup")
def startup():
    if wa_engine is not None:
        try:init_db()
        except Exception as e: print("WhatsApp Live Bridge init warning:",e)

@router.post("/sources/account")
def add_account(label:str=Form(...),phone:str=Form(...)):
    init_db()
    aid=_account_id(phone.strip())
    with wa_engine.begin() as c:
        c.execute(text("""INSERT INTO wa_bridge_accounts(account_id,label,phone,active)
        VALUES(:a,:l,:p,TRUE) ON CONFLICT(phone) DO UPDATE SET label=EXCLUDED.label,active=TRUE,updated_at=NOW()"""),
        {"a":aid,"l":label.strip(),"p":phone.strip()})
    return RedirectResponse("/whatsapp-live/sources",303)

@router.post("/sources/group")
def add_group(account_id:str=Form(...),group_name:str=Form(...)):
    init_db()
    gid=_group_id(account_id,group_name.strip())
    sid=_source_uuid(gid)
    with wa_engine.begin() as c:
        c.execute(text("""INSERT INTO wa_bridge_groups(group_id,account_id,group_name,active,auto_process,source_id)
        VALUES(:g,:a,:n,TRUE,TRUE,:s) ON CONFLICT(account_id,group_name)
        DO UPDATE SET active=TRUE,auto_process=TRUE,updated_at=NOW()"""),
        {"g":gid,"a":account_id,"n":group_name.strip(),"s":sid})
        _ensure_source(c,sid,group_name.strip())
    return RedirectResponse("/whatsapp-live/sources",303)

@router.post("/sources/group/{group_id}/toggle")
def toggle_group(group_id:str):
    init_db()
    with wa_engine.begin() as c:
        c.execute(text("UPDATE wa_bridge_groups SET active=NOT active,updated_at=NOW() WHERE group_id=:g"),{"g":group_id})
    return RedirectResponse("/whatsapp-live/sources",303)

@router.post("/api/ingest")
async def ingest(req:Request,authorization:Optional[str]=Header(None),x_bridge_token:Optional[str]=Header(None)):
    _auth(authorization,x_bridge_token)
    init_db()
    payload=await req.json()
    account_phone=str(payload.get("account_phone") or "").strip()
    group_name=str(payload.get("group_name") or "").strip()
    if not account_phone or not group_name or not str(payload.get("text") or "").strip():
        raise HTTPException(400,"account_phone, group_name and text are required")
    with wa_engine.begin() as c:
        acct=c.execute(text("SELECT * FROM wa_bridge_accounts WHERE phone=:p AND active=TRUE"),{"p":account_phone}).mappings().first()
        if not acct:
            raise HTTPException(403,"This mobile number is not added/active in WhatsApp Sources")
        group=c.execute(text("""SELECT * FROM wa_bridge_groups WHERE account_id=:a AND group_name=:n AND active=TRUE AND auto_process=TRUE"""),
                        {"a":acct["account_id"],"n":group_name}).mappings().first()
        if not group:
            raise HTTPException(403,"This group is not added/active for this mobile number")
        external=str(payload.get("message_id") or "").strip() or hashlib.sha256(
            f"{group['group_id']}|{payload.get('timestamp')}|{payload.get('sender_phone')}|{payload.get('text')}".encode()).hexdigest()
        eid="WAE-"+hashlib.sha256(f"{group['group_id']}|{external}".encode()).hexdigest()[:16].upper()
        exists=c.execute(text("SELECT status,classification,entity_id FROM wa_bridge_events WHERE event_id=:e"),{"e":eid}).mappings().first()
        if exists:
            return {"status":"duplicate","event_id":eid,"classification":exists["classification"],"entity_id":exists["entity_id"]}
        c.execute(text("""INSERT INTO wa_bridge_events(event_id,group_id,external_message_id,sender_name,sender_phone,message_timestamp,raw_text,status)
        VALUES(:e,:g,:x,:sn,:sp,:ts,:raw,'PROCESSING')"""),{
            "e":eid,"g":group["group_id"],"x":external,"sn":payload.get("sender_name"),"sp":payload.get("sender_phone"),
            "ts":payload.get("timestamp"),"raw":payload.get("text")})
        try:
            classification,entity_id=_process(c,group,{
                "text":payload.get("text"),"sender_name":payload.get("sender_name"),
                "sender_phone":payload.get("sender_phone"),"timestamp":payload.get("timestamp")
            })
            counters={"PROPERTY_INVENTORY":"properties_found","PROPERTY_REQUIREMENT":"requirements_found","REJECTED":"rejected_found"}
            counter=counters.get(classification)
            setpart=f",{counter}={counter}+1" if counter else ""
            c.execute(text(f"""UPDATE wa_bridge_groups SET messages_received=messages_received+1,last_message_at=NOW(){setpart},updated_at=NOW()
            WHERE group_id=:g"""),{"g":group["group_id"]})
            c.execute(text("""UPDATE wa_bridge_events SET status='PROCESSED',classification=:cl,entity_id=:ent,processed_at=NOW()
            WHERE event_id=:e"""),{"cl":classification,"ent":entity_id,"e":eid})
            c.execute(text("""UPDATE wa_sources SET total_messages=total_messages+1,processed_at=NOW() WHERE source_id=:s"""),{"s":group["source_id"]})
            return {"status":"processed","event_id":eid,"classification":classification,"entity_id":entity_id}
        except Exception as e:
            c.execute(text("""UPDATE wa_bridge_events SET status='FAILED',error_message=:err,processed_at=NOW() WHERE event_id=:e"""),
                      {"err":str(e)[:1200],"e":eid})
            raise

def _date_bounds(preset,from_date,to_date):
    now=datetime.now(timezone.utc)
    if preset=="today":
        a=now.replace(hour=0,minute=0,second=0,microsecond=0);b=a+timedelta(days=1)
    elif preset=="yesterday":
        b=now.replace(hour=0,minute=0,second=0,microsecond=0);a=b-timedelta(days=1)
    elif preset in {"3d","7d","30d"}:
        days=int(preset[:-1]);b=now+timedelta(days=1);a=now-timedelta(days=days)
    elif from_date or to_date:
        try:a=datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc) if from_date else datetime(2000,1,1,tzinfo=timezone.utc)
        except:a=datetime(2000,1,1,tzinfo=timezone.utc)
        try:b=datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)+timedelta(days=1) if to_date else now+timedelta(days=1)
        except:b=now+timedelta(days=1)
    else:
        a=datetime(2000,1,1,tzinfo=timezone.utc);b=now+timedelta(days=1)
    return a,b

def _page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{esc(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#f4f6f8;color:#101828}}header{{background:#101828;color:#fff;padding:18px 24px}}
    nav{{background:#fff;padding:10px 18px;border-bottom:1px solid #e4e7ec;display:flex;gap:8px;flex-wrap:wrap}}nav a{{text-decoration:none;color:#344054;padding:8px 10px;border-radius:7px}}nav a:hover{{background:#101828;color:#fff}}
    main{{max-width:1500px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}.card{{background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:14px;margin-bottom:12px}}
    table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}}input,select{{padding:9px;border:1px solid #d0d5dd;border-radius:7px;width:100%}}
    button,.btn{{border:0;background:#101828;color:#fff;padding:9px 12px;border-radius:7px;text-decoration:none;cursor:pointer}}.green{{background:#039855}}.muted{{color:#667085}}pre{{white-space:pre-wrap;margin:0}}
    </style></head><body><header><h2 style='margin:0'>WhatsApp Live Property Intelligence</h2><small>Approved numbers + approved groups → automatic intake → property/requirement → match → hot lead → team review</small></header>
    <nav><a href='/whatsapp-live'>Live Dashboard</a><a href='/whatsapp-live/sources'>WhatsApp Sources</a><a href='/whatsapp-live/feed'>Live Feed</a><a href='/whatsapp-live/requirements'>Date-wise Requirements</a><a href='/whatsapp-intelligence'>Main WhatsApp Intelligence</a><a href='/whatsapp-automation'>Hot Leads</a></nav>
    <main>{body}</main></body></html>"""

@router.get("",response_class=HTMLResponse)
def dashboard():
    init_db()
    with wa_engine.begin() as c:
        stats={
            "accounts":c.execute(text("SELECT COUNT(*) FROM wa_bridge_accounts WHERE active=TRUE")).scalar() or 0,
            "groups":c.execute(text("SELECT COUNT(*) FROM wa_bridge_groups WHERE active=TRUE")).scalar() or 0,
            "today":c.execute(text("SELECT COUNT(*) FROM wa_bridge_events WHERE created_at>=CURRENT_DATE")).scalar() or 0,
            "failed":c.execute(text("SELECT COUNT(*) FROM wa_bridge_events WHERE status='FAILED' AND created_at>=CURRENT_DATE")).scalar() or 0,
        }
    body=f"""<h2>Live Intake Command Centre</h2><div class=grid>
    <div class=card>Active Mobile Numbers<h2>{stats['accounts']}</h2></div><div class=card>Active Groups<h2>{stats['groups']}</h2></div>
    <div class=card>Messages Today<h2>{stats['today']}</h2></div><div class=card>Failed Today<h2>{stats['failed']}</h2></div></div>
    <div class=card><h3>How automation works</h3><p>Add a mobile number once, add each approved WhatsApp group once, then keep the phone bridge running. New messages from active groups are accepted automatically, purified, classified, saved, matched and converted to Hot Leads when appropriate. Outbound messages remain team-review only.</p>
    <p><a class='btn green' href='/whatsapp-live/sources'>Add Number / Group</a> <a class=btn href='/whatsapp-live/requirements'>Check Date-wise Requirements</a></p></div>"""
    return HTMLResponse(_page("WhatsApp Live Dashboard",body))

@router.get("/sources",response_class=HTMLResponse)
def sources():
    init_db()
    with wa_engine.begin() as c:
        accounts=c.execute(text("SELECT * FROM wa_bridge_accounts ORDER BY id DESC")).mappings().all()
        groups=c.execute(text("""SELECT g.*,a.label account_label,a.phone account_phone FROM wa_bridge_groups g
        JOIN wa_bridge_accounts a ON a.account_id=g.account_id ORDER BY g.id DESC""")).mappings().all()
    opts="".join(f"<option value='{esc(a['account_id'])}'>{esc(a['label'])} · {esc(a['phone'])}</option>" for a in accounts if a["active"])
    rows="".join(f"""<tr><td>{esc(g['account_label'])}<br><small>{esc(g['account_phone'])}</small></td><td>{esc(g['group_name'])}</td>
    <td>{'ACTIVE' if g['active'] else 'PAUSED'}</td><td>{g['messages_received']}</td><td>{g['properties_found']}</td><td>{g['requirements_found']}</td>
    <td>{esc(g['last_message_at'] or '—')}</td><td><form method=post action='/whatsapp-live/sources/group/{esc(g['group_id'])}/toggle'><button>{'Pause' if g['active'] else 'Activate'}</button></form></td></tr>""" for g in groups)
    body=f"""<h2>WhatsApp Sources</h2><div class=grid>
    <div class=card><h3>Add Mobile Number</h3><form method=post action='/whatsapp-live/sources/account'><p><input name=label placeholder='Office Mobile 1' required></p><p><input name=phone placeholder='+9198XXXXXXXX' required></p><button class=green>Add / Activate Number</button></form></div>
    <div class=card><h3>Add WhatsApp Group</h3><form method=post action='/whatsapp-live/sources/group'><p><select name=account_id required><option value=''>Choose number</option>{opts}</select></p><p><input name=group_name placeholder='Exact WhatsApp group name' required></p><button class=green>Add / Activate Group</button></form></div></div>
    <div class=card><h3>Connected Sources</h3><table><tr><th>Mobile</th><th>Group</th><th>Status</th><th>Messages</th><th>Properties</th><th>Requirements</th><th>Last Sync</th><th>Action</th></tr>{rows}</table></div>"""
    return HTMLResponse(_page("WhatsApp Sources",body))

@router.get("/feed",response_class=HTMLResponse)
def feed():
    init_db()
    with wa_engine.begin() as c:
        rows=c.execute(text("""SELECT e.*,g.group_name,a.label account_label FROM wa_bridge_events e
        JOIN wa_bridge_groups g ON g.group_id=e.group_id JOIN wa_bridge_accounts a ON a.account_id=g.account_id
        ORDER BY e.id DESC LIMIT 300""")).mappings().all()
    trs="".join(f"""<tr><td>{esc(r['created_at'])}</td><td>{esc(r['account_label'])}</td><td>{esc(r['group_name'])}</td><td>{esc(r['sender_name'] or r['sender_phone'])}</td>
    <td><pre>{esc(r['raw_text'])}</pre></td><td>{esc(r['classification'] or r['status'])}</td><td>{esc(r['entity_id'] or '—')}</td></tr>""" for r in rows)
    return HTMLResponse(_page("Live Feed",f"<h2>Live Feed</h2><div class=card><table><tr><th>Received</th><th>Mobile</th><th>Group</th><th>Sender</th><th>Message</th><th>Result</th><th>Entity</th></tr>{trs}</table></div>"))

@router.get("/requirements",response_class=HTMLResponse)
def dated_requirements(request:Request):
    init_db()
    preset=str(request.query_params.get("preset") or "7d")
    from_date=str(request.query_params.get("from") or "")
    to_date=str(request.query_params.get("to") or "")
    sort=str(request.query_params.get("sort") or "newest")
    a,b=_date_bounds(preset,from_date,to_date)
    order="best_score DESC, req_time DESC" if sort=="hot" else "req_time ASC" if sort=="oldest" else "req_time DESC"
    with wa_engine.begin() as c:
        rows=c.execute(text(f"""SELECT r.*,COALESCE(m.message_timestamp,r.created_at::text) req_time,
        COALESCE(ms.best_score,0) best_score,COALESCE(ms.match_count,0) match_count
        FROM wa_requirements r LEFT JOIN wa_messages m ON m.message_id=r.message_id
        LEFT JOIN (SELECT wa_requirement_id,MAX(score) best_score,COUNT(*) match_count FROM wa_matches GROUP BY wa_requirement_id) ms
        ON ms.wa_requirement_id=r.wa_requirement_id
        WHERE r.status='ACTIVE' AND r.created_at>=:a AND r.created_at<:b
        ORDER BY {order} LIMIT 1000"""),{"a":a,"b":b}).mappings().all()
    cards="".join(f"""<div class=card><div style='display:flex;justify-content:space-between;gap:10px'><div><b>{esc(r['req_time'])}</b><br><small>{esc(r['wa_requirement_id'])}</small></div>
    <div><b>{float(r['best_score'] or 0):.0f}% best match</b><br><small>{int(r['match_count'] or 0)} properties</small></div></div>
    <h3>{esc(r['preferred_locations'] or 'UNKNOWN')} · {esc(r['property_type'] or 'UNKNOWN')}</h3><pre>{esc(r['raw_text'])}</pre>
    <p><b>Budget:</b> {money(r['budget_max_inr'])} · <b>Phone:</b> {esc(r['contact_phone'] or '—')}</p>
    <a class=btn href='/whatsapp-intelligence/requirement/{esc(r['wa_requirement_id'])}/matches'>View Matches</a></div>""" for r in rows)
    body=f"""<h2>Date-wise Requirements</h2><div class=card><form method=get style='display:grid;grid-template-columns:repeat(5,1fr);gap:8px'>
    <select name=preset><option value='today'>Today</option><option value='yesterday'>Yesterday</option><option value='3d'>Last 3 Days</option><option value='7d' {'selected' if preset=='7d' else ''}>Last 7 Days</option><option value='30d'>Last 30 Days</option><option value='custom'>Custom</option></select>
    <input type=date name=from value='{esc(from_date)}'><input type=date name=to value='{esc(to_date)}'>
    <select name=sort><option value='newest'>Newest First</option><option value='oldest' {'selected' if sort=='oldest' else ''}>Oldest First</option><option value='hot' {'selected' if sort=='hot' else ''}>Highest Match First</option></select>
    <button>Apply</button></form></div><p class=muted>{len(rows)} requirements in selected period.</p>{cards or '<div class=card>No requirements in this date range.</div>'}"""
    return HTMLResponse(_page("Date-wise Requirements",body))

@router.get("/health")
def health():
    init_db()
    with wa_engine.begin() as c:c.execute(text("SELECT 1"))
    return {"ok":True,"bridge_token_configured":bool(BRIDGE_TOKEN),"outbound":"TEAM_REVIEW_ONLY"}
