from __future__ import annotations
import html, json, re, threading, time
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="7.3.7-ALLIANCE-HISTORICAL-EVIDENCE-REPAIR"
MODE="V721_CERTIFIED_PRIMARY_TEAM_WORKSPACE_VERIFY_ASSIGN_MATCH_ALTERNATIVES_REVIEW_CLIENT_SAFE_DRAFT_FOLLOWUP_SOURCE_EVIDENCE_NO_CANONICAL_MUTATION"
STATE={"status":"STARTING","started_at":datetime.now(timezone.utc).isoformat(),"result":None,"last_error":None}
_LOCK=threading.Lock()

DDL=[
"""CREATE TABLE IF NOT EXISTS pi_master_action_state_v730(
 canonical_id TEXT PRIMARY KEY,
 entity_type TEXT NOT NULL,
 stage TEXT NOT NULL DEFAULT 'NEW',
 assigned_to TEXT,
 next_followup_at TIMESTAMPTZ,
 followup_status TEXT NOT NULL DEFAULT 'NOT_SCHEDULED',
 review_status TEXT NOT NULL DEFAULT 'READY_FOR_REVIEW',
 internal_notes TEXT,
 updated_at TIMESTAMPTZ DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS pi_master_action_log_v730(
 id BIGSERIAL PRIMARY KEY,
 canonical_id TEXT NOT NULL,
 entity_type TEXT NOT NULL,
 action TEXT NOT NULL,
 actor TEXT,
 details JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ DEFAULT NOW())""",
"""CREATE INDEX IF NOT EXISTS idx_v730_action_log_entity ON pi_master_action_log_v730(canonical_id,created_at DESC)""",
"""CREATE TABLE IF NOT EXISTS pi_match_reviews_v730(
 requirement_canonical_id TEXT NOT NULL,
 property_canonical_id TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'READY_FOR_REVIEW',
 reviewed_by TEXT,
 reviewed_at TIMESTAMPTZ,
 notes TEXT,
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 PRIMARY KEY(requirement_canonical_id,property_canonical_id))"""
]

def _engine(core): return getattr(core,"engine",None)
def _app(core): return getattr(core,"app",None) or core
def _role(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _actor(core,req):
    fn=getattr(core,"actor_name",None)
    return fn(req) if fn else "team"
def _safe(v):
    if v is None or isinstance(v,(str,int,float,bool)): return v
    if isinstance(v,Decimal): return float(v)
    if isinstance(v,datetime): return v.isoformat()
    if isinstance(v,dict): return {str(k):_safe(x) for k,x in v.items()}
    if isinstance(v,(list,tuple,set)): return [_safe(x) for x in v]
    return str(v)
def _rows(rs): return [{k:_safe(v) for k,v in dict(r._mapping).items()} for r in rs]
def _route_exists(app,path):
    return any(getattr(r,"path",None)==path for r in getattr(app,"routes",[]))
def _audit_log(engine,cid,etype,action,actor,details=None):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pi_master_action_log_v730(canonical_id,entity_type,action,actor,details)
          VALUES(:id,:et,:a,:by,CAST(:d AS JSONB))"""),
          {"id":cid,"et":etype,"a":action,"by":actor,"d":json.dumps(details or {},ensure_ascii=False,default=str)})
def _get_action(engine,cid):
    with engine.connect() as c:
        r=c.execute(text("SELECT * FROM pi_master_action_state_v730 WHERE canonical_id=:id"),{"id":cid}).mappings().first()
    return _safe(dict(r)) if r else {}
def _set_action(engine,cid,etype,actor,**fields):
    allowed={"stage","assigned_to","next_followup_at","followup_status","review_status","internal_notes"}
    clean={k:v for k,v in fields.items() if k in allowed}
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pi_master_action_state_v730(canonical_id,entity_type,stage,assigned_to,next_followup_at,followup_status,review_status,internal_notes,updated_at)
          VALUES(:id,:et,:stage,:assigned,:nextf,:fstatus,:rstatus,:notes,NOW())
          ON CONFLICT(canonical_id) DO UPDATE SET
          stage=COALESCE(EXCLUDED.stage,pi_master_action_state_v730.stage),
          assigned_to=COALESCE(EXCLUDED.assigned_to,pi_master_action_state_v730.assigned_to),
          next_followup_at=COALESCE(EXCLUDED.next_followup_at,pi_master_action_state_v730.next_followup_at),
          followup_status=COALESCE(EXCLUDED.followup_status,pi_master_action_state_v730.followup_status),
          review_status=COALESCE(EXCLUDED.review_status,pi_master_action_state_v730.review_status),
          internal_notes=COALESCE(EXCLUDED.internal_notes,pi_master_action_state_v730.internal_notes),
          updated_at=NOW()"""),
          {"id":cid,"et":etype,"stage":clean.get("stage"),"assigned":clean.get("assigned_to"),
           "nextf":clean.get("next_followup_at"),"fstatus":clean.get("followup_status"),
           "rstatus":clean.get("review_status"),"notes":clean.get("internal_notes")})
    _audit_log(engine,cid,etype,"WORKFLOW_UPDATED",actor,clean)

def _counts(engine):
    with engine.connect() as c:
        return {
          "properties":c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v711")).scalar_one(),
          "requirements":c.execute(text("SELECT COUNT(*) FROM pi_master_requirements_v711")).scalar_one(),
          "verified":c.execute(text("SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE verification_status='VERIFIED'")).scalar_one(),
          "matches":c.execute(text("SELECT COUNT(*) FROM pi_master_matches_v720")).scalar_one(),
          "followups":c.execute(text("SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE followup_status='SCHEDULED'")).scalar_one(),
          "assigned":c.execute(text("SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE assigned_to IS NOT NULL AND assigned_to<>''")).scalar_one(),
        }

PRIMARY_NAV=[
("Command Centre","/alliance/primary"),
("Properties","/alliance/primary/properties"),
("Requirements","/alliance/primary/requirements"),
("Availability","/alliance/primary/availability"),
("Matcher","/alliance/primary/matcher"),
("Follow-ups","/alliance/primary/followups"),
("Data Repair","/alliance/primary/data-repair"),
("Add Property","/property-manual"),
]

def _shell(core,req,title,body):
    role=_role(core,req)
    nav="".join(f'<a href="{html.escape(p,quote=True)}">{html.escape(l)}</a>' for l,p in PRIMARY_NAV)
    admin=""
    if role=="admin":
        admin="""<details class="admin"><summary>Admin / Legacy</summary>
        <a href="/alliance">7.2 Command Centre</a>
        <a href="/workspace">Legacy Workspace</a>
        <a href="/database-page?table_name=properties">Legacy Property DB</a>
        <a href="/database-page?table_name=requirements">Legacy Requirement DB</a>
        <a href="/property-brain/acceptance-v721">7.2.1 Acceptance</a>
        <a href="/status-page">System Status</a></details>"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
*{{box-sizing:border-box}}body{{font-family:Arial,sans-serif;margin:0;background:#f4f7fb;color:#172033}}
header{{background:#0d2238;color:white;padding:18px 22px;display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap}}
nav{{background:white;border-bottom:1px solid #dfe6ee;padding:10px 14px;display:flex;gap:7px;flex-wrap:wrap;position:sticky;top:0;z-index:2}}
nav a,.btn,.mini{{background:#0d2238;color:white;text-decoration:none;border:0;border-radius:8px;padding:9px 11px;cursor:pointer;display:inline-block}}
.btn.alt,.mini.alt{{background:#475467}}.btn.good,.mini.good{{background:#067647}}.btn.warn,.mini.warn{{background:#b54708}}
.wrap{{max-width:1800px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
.card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin-bottom:12px}}.num{{font-size:28px;font-weight:800}}
.tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:#f8fafc}}input,select,textarea{{padding:8px;border:1px solid #cfd8e3;border-radius:7px;max-width:100%}}
form.inline{{display:flex;gap:7px;flex-wrap:wrap;align-items:center}}.muted{{color:#667085}}.ok{{color:#08783e;font-weight:700}}.warntext{{color:#b54708;font-weight:700}}
.bad{{color:#b42318;font-weight:700}}.pill{{padding:3px 7px;border-radius:999px;background:#eef2f6;white-space:nowrap}}pre{{white-space:pre-wrap;word-break:break-word}}
details.admin{{background:#fff;border:1px solid #dfe6ee;padding:8px 12px}}details.admin a{{margin:5px;display:inline-block}}
.actions{{display:flex;gap:5px;flex-wrap:wrap}}.right{{text-align:right}}
</style></head><body>
<header><div><b>Alliance CRE Operating System · 7.3.7</b><br><small>Capture Evidence → Structure → Assign → Verify → Match → Review → Follow-up</small></div>
<div>{html.escape(str(role))} · <a href="/logout" style="color:white">Logout</a></div></header>
<nav>{nav}</nav>{admin}<div class="wrap"><h2>{html.escape(title)}</h2>{body}</div></body></html>"""

def _property(engine,cid):
    import alliance_master_integration_v720 as v720
    with engine.connect() as c:
        r=c.execute(text("""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
          w.verified_at,w.verified_by,w.availability_status,w.assigned_to workflow_assigned_to,w.internal_notes workflow_notes
          FROM pi_master_properties_v711 p LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
          WHERE p.canonical_id=:id"""),{"id":cid}).mappings().first()
    return v720._decorate_property(_safe(dict(r))) if r else None
def _requirement(engine,cid):
    import alliance_master_integration_v720 as v720
    with engine.connect() as c:
        r=c.execute(text("""SELECT r.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
          w.verified_at,w.verified_by,w.assigned_to workflow_assigned_to,w.internal_notes workflow_notes
          FROM pi_master_requirements_v711 r LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
          WHERE r.canonical_id=:id"""),{"id":cid}).mappings().first()
    return v720._decorate_requirement(_safe(dict(r))) if r else None

def _verify_property(engine,cid,actor):
    if not _property(engine,cid): raise HTTPException(404,"Property not found")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pi_master_workflow_v720(canonical_id,entity_type,verification_status,verified_at,verified_by,availability_status,updated_at)
          VALUES(:id,'PROPERTY','VERIFIED',NOW(),:by,'AVAILABLE',NOW())
          ON CONFLICT(canonical_id) DO UPDATE SET verification_status='VERIFIED',verified_at=NOW(),verified_by=:by,
          availability_status='AVAILABLE',updated_at=NOW()"""),{"id":cid,"by":actor})
    _set_action(engine,cid,"PROPERTY",actor,stage="VERIFIED")
    _audit_log(engine,cid,"PROPERTY","VERIFIED_AVAILABLE",actor,{})
def _mark_unavailable(engine,cid,actor):
    if not _property(engine,cid): raise HTTPException(404,"Property not found")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pi_master_workflow_v720(canonical_id,entity_type,verification_status,availability_status,updated_at)
          VALUES(:id,'PROPERTY','UNVERIFIED','UNAVAILABLE',NOW())
          ON CONFLICT(canonical_id) DO UPDATE SET availability_status='UNAVAILABLE',verification_status='UNVERIFIED',updated_at=NOW()"""),{"id":cid})
    _set_action(engine,cid,"PROPERTY",actor,stage="UNAVAILABLE")
    _audit_log(engine,cid,"PROPERTY","MARKED_UNAVAILABLE",actor,{})

def _source_links(engine,cid):
    with engine.connect() as c:
        return _rows(c.execute(text("""SELECT source_type,source_table,source_pk,source_row_hash,created_at
          FROM pi_master_source_links_v711 WHERE canonical_id=:id ORDER BY id"""),{"id":cid}))
def _logs(engine,cid,limit=50):
    with engine.connect() as c:
        return _rows(c.execute(text("""SELECT action,actor,details,created_at FROM pi_master_action_log_v730
          WHERE canonical_id=:id ORDER BY id DESC LIMIT :n"""),{"id":cid,"n":limit}))

def _match_full(engine,rid,limit=50):
    """Full master inventory. Exact locality first; then transparent same-city and broader transaction/area alternatives."""
    import alliance_master_integration_v720 as v720
    req=_requirement(engine,rid)
    if not req: raise HTTPException(404,"Requirement not found")
    tx=req.get("transaction_type") or ""
    props=v720._search_properties(engine,tx=tx,limit=4000)
    exact=[];same_city=[];broader=[]
    rl=(req.get("locality") or "").strip().lower()
    rc=(req.get("city") or "").strip().lower()
    for p in props:
        if p.get("availability_status")=="UNAVAILABLE": continue
        score,reasons=v720._score(req,p)
        pl=(p.get("locality") or "").strip().lower(); pc=(p.get("city") or "").strip().lower()
        if rl and pl and (rl in pl or pl in rl):
            tier="EXACT_LOCALITY"; bonus=10
        elif rc and pc and rc==pc:
            tier="SAME_CITY_ALTERNATIVE"; bonus=3
        else:
            tier="TRANSACTION_AREA_ALTERNATIVE"; bonus=0
        # If v720 score is low because locality differs, preserve useful area-fit alternatives.
        area_req=req.get("area_sqft");area_prop=p.get("area_sqft")
        area_fit=False
        if area_req and area_prop:
            diff=abs(float(area_prop)-float(area_req))/max(float(area_req),1)
            area_fit=diff<=0.50
        if tier=="EXACT_LOCALITY" or score>=35 or (tier!="EXACT_LOCALITY" and area_fit and tx==p.get("transaction_type")):
            item={"score":min(100,score+bonus),"base_score":score,"tier":tier,"reasons":reasons,"property":p}
            (exact if tier=="EXACT_LOCALITY" else same_city if tier=="SAME_CITY_ALTERNATIVE" else broader).append(item)
    for bucket in (exact,same_city,broader):
        bucket.sort(key=lambda x:(x["score"], x["property"].get("verification_status")=="VERIFIED"),reverse=True)
    combined=(exact+same_city+broader)[:limit]
    with engine.begin() as c:
        for m in combined:
            p=m["property"]
            c.execute(text("""INSERT INTO pi_master_matches_v720(requirement_canonical_id,property_canonical_id,match_score,match_reasons,status,updated_at)
              VALUES(:r,:p,:s,CAST(:why AS JSONB),'READY_FOR_REVIEW',NOW())
              ON CONFLICT(requirement_canonical_id,property_canonical_id) DO UPDATE SET
              match_score=EXCLUDED.match_score,match_reasons=EXCLUDED.match_reasons,status='READY_FOR_REVIEW',updated_at=NOW()"""),
              {"r":rid,"p":p["canonical_id"],"s":m["score"],"why":json.dumps([m["tier"]]+m["reasons"])})
            c.execute(text("""INSERT INTO pi_match_reviews_v730(requirement_canonical_id,property_canonical_id,review_status)
              VALUES(:r,:p,'READY_FOR_REVIEW') ON CONFLICT DO NOTHING"""),{"r":rid,"p":p["canonical_id"]})
    return req,combined

def _approved_matches(engine,rid):
    import alliance_master_integration_v720 as v720
    with engine.connect() as c:
        rows=c.execute(text("""SELECT p.*,m.match_score,m.match_reasons,rv.review_status,
          COALESCE(w.verification_status,'UNVERIFIED') verification_status,w.availability_status
          FROM pi_master_matches_v720 m
          JOIN pi_master_properties_v711 p ON p.canonical_id=m.property_canonical_id
          LEFT JOIN pi_match_reviews_v730 rv ON rv.requirement_canonical_id=m.requirement_canonical_id AND rv.property_canonical_id=m.property_canonical_id
          LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
          WHERE m.requirement_canonical_id=:r
          ORDER BY m.match_score DESC"""),{"r":rid}).mappings().all()
    out=[]
    for r in rows:
        d=_safe(dict(r)); d=v720._decorate_property(d)
        if d.get("review_status")=="APPROVED" and d.get("verification_status")=="VERIFIED" and d.get("availability_status")!="UNAVAILABLE":
            out.append(d)
    return out

def _draft(req,props):
    if not props:
        return "No client draft available yet. Approve and verify at least one matched property first."
    lines=["Property options matching your requirement:"]
    for i,p in enumerate(props[:3],1):
        bits=[f"{i}. {p.get('locality') or 'Property option'}"]
        if p.get("area_sqft_display"):bits.append(f"{p['area_sqft_display']} sq ft")
        if p.get("transaction_type"):bits.append(str(p["transaction_type"]))
        if p.get("sale_amount"):bits.append("Sale: "+str(p["sale_amount"]))
        if p.get("rent_amount"):bits.append("Rent: "+str(p["rent_amount"]))
        lines.append(" | ".join(bits))
    lines.append("Please let us know which option you would like to inspect or schedule a site visit for.")
    return "\n".join(lines)

# 7.3.2 FULL SOURCE EVIDENCE
def _v732_columns(engine,table):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$",table or ""): return []
    with engine.connect() as c:
        return [x[0] for x in c.execute(text("""SELECT column_name FROM information_schema.columns
          WHERE table_schema=current_schema() AND table_name=:t ORDER BY ordinal_position"""),{"t":table}).all()]

def _v732_fetch_row(engine,table,source_pk):
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$",table or ""): return None
    cols=_v732_columns(engine,table)
    if not cols:return None
    candidates=[x for x in ["id","source_id","message_id","listing_id","property_id","requirement_id","pk"] if x in cols]
    for key in candidates:
        try:
            with engine.connect() as c:
                row=c.execute(text(f'SELECT * FROM "{table}" WHERE CAST("{key}" AS TEXT)=:v LIMIT 1'),{"v":str(source_pk)}).mappings().first()
            if row:return _safe(dict(row))
        except Exception: pass
    return None

def _v732_pick(d,names):
    low={str(k).lower():k for k in (d or {}).keys()}
    for n in names:
        if n.lower() in low:
            v=d.get(low[n.lower()])
            if v not in (None,"",[],{}):return v
    return None

def _v732_table_exists(engine,t):
    try:
        with engine.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
    except Exception:return False

def _v732_follow_whatsapp(engine,row):
    if not isinstance(row,dict):return []
    out=[];ids=[]
    for k in ["source_message_id","raw_message_id","message_id","whatsapp_message_id"]:
        v=_v732_pick(row,[k])
        if v not in (None,""):ids.append(v)
    if _v732_table_exists(engine,"wai_raw_messages"):
        cols=_v732_columns(engine,"wai_raw_messages")
        keys=[x for x in ["id","message_id"] if x in cols]
        for mid in ids:
            for key in keys:
                try:
                    with engine.connect() as c:
                        rr=c.execute(text(f'SELECT * FROM "wai_raw_messages" WHERE CAST("{key}" AS TEXT)=:v LIMIT 1'),{"v":str(mid)}).mappings().first()
                    if rr:
                        out.append({"table":"wai_raw_messages","row":_safe(dict(rr))});break
                except Exception:pass
    lid=_v732_pick(row,["listing_id","wai_listing_id"])
    if lid and _v732_table_exists(engine,"wai_listings"):
        lr=_v732_fetch_row(engine,"wai_listings",lid)
        if lr:
            out.append({"table":"wai_listings","row":lr})
            mid=_v732_pick(lr,["source_message_id","raw_message_id","message_id"])
            if mid and _v732_table_exists(engine,"wai_raw_messages"):
                rr=_v732_fetch_row(engine,"wai_raw_messages",mid)
                if rr:out.append({"table":"wai_raw_messages","row":rr})
    seen=set();ded=[]
    for x in out:
        sig=(x["table"],json.dumps(x["row"],sort_keys=True,default=str))
        if sig not in seen:seen.add(sig);ded.append(x)
    return ded

def _v732_evidence(engine,cid):
    links=_source_links(engine,cid);blocks=[]
    for link in links:
        table=str(link.get("source_table") or "");pk=link.get("source_pk")
        row=_v732_fetch_row(engine,table,pk)
        lineage=_v732_follow_whatsapp(engine,row) if row else []
        candidates=([{"table":table,"row":row}] if row else [])+lineage
        preferred=next((x for x in candidates if x.get("table")=="wai_raw_messages" and isinstance(x.get("row"),dict)),None)
        if not preferred and candidates:preferred=candidates[0]
        d=(preferred or {}).get("row") or {}
        blocks.append({"link":link,"source_row":row,"lineage":lineage,"display":{
          "source_type":link.get("source_type"),
          "group":_v732_pick(d,["group_name","chat_name","conversation_name","source_group","group_title"]),
          "sender":_v732_pick(d,["sender_name","push_name","contact_name","sender","author_name","name"]),
          "sender_phone":_v732_pick(d,["sender_phone","phone","phone_number","sender_number","contact_phone","author_phone"]),
          "sender_jid":_v732_pick(d,["sender_jid","jid","author","participant","remote_jid"]),
          "message_timestamp":_v732_pick(d,["message_timestamp","timestamp","sent_at","message_date","datetime","created_at","received_at"]),
          "full_message":_v732_pick(d,["message_text","text","body","content","message","raw_text","full_message","caption"])}})
    return blocks

def _v732_evidence_html(engine,cid):
    ev=_v732_evidence(engine,cid)
    if not ev:return "<div class='card'><h3>Original Source / WhatsApp Message</h3><p>No recoverable original-source row is linked to this canonical record yet.</p></div>"
    out=[]
    for i,e in enumerate(ev,1):
        d=e["display"];link=e["link"];msg=d.get("full_message")
        if isinstance(msg,(dict,list)):msg=json.dumps(msg,ensure_ascii=False,indent=2)
        lineage_html=""
        for ln in e.get("lineage") or []:
            lineage_html+=f"<details><summary>{html.escape(str(ln.get('table')))} raw lineage</summary><pre>{html.escape(json.dumps(ln.get('row') or {},ensure_ascii=False,indent=2,default=str))}</pre></details>"
        source_json=json.dumps(e.get("source_row") or {},ensure_ascii=False,indent=2,default=str)
        out.append(f"""<div class='card'>
        <h3>Original Source / WhatsApp Message · Evidence {i}</h3>
        <div class='grid'>
          <div><b>Source Type</b><br>{html.escape(str(d.get('source_type') or ''))}</div>
          <div><b>WhatsApp Group / Chat</b><br>{html.escape(str(d.get('group') or 'Not captured'))}</div>
          <div><b>Sender Name</b><br>{html.escape(str(d.get('sender') or 'Not captured'))}</div>
          <div><b>Sender Phone</b><br>{html.escape(str(d.get('sender_phone') or 'Not captured'))}</div>
          <div><b>Sender JID / Identity</b><br>{html.escape(str(d.get('sender_jid') or 'Not captured'))}</div>
          <div><b>Message Date & Time</b><br>{html.escape(str(d.get('message_timestamp') or 'Not captured'))}</div>
        </div>
        <h4>Full Original Message</h4>
        <pre style='font-size:14px;background:#f8fafc;border:1px solid #e1e7ee;padding:14px;border-radius:9px'>{html.escape(str(msg or 'Original message text not available in this source row'))}</pre>
        <p class='muted'><b>Canonical evidence link:</b> {html.escape(str(link.get('source_table') or ''))} · PK {html.escape(str(link.get('source_pk') or ''))} · Hash {html.escape(str(link.get('source_row_hash') or ''))}</p>
        <details><summary>All original source fields</summary><pre>{html.escape(source_json)}</pre></details>
        {lineage_html}</div>""")
    return "".join(out)


# 7.3.3 REQUIREMENT OPERATIONS
def _v733_action_map(engine, entity_type="REQUIREMENT"):
    try:
        with engine.connect() as c:
            rows = c.execute(text("""SELECT * FROM pi_master_action_state_v730
              WHERE entity_type=:et ORDER BY updated_at DESC"""), {"et": entity_type}).mappings().all()
        return {str(r["canonical_id"]): _safe(dict(r)) for r in rows}
    except Exception:
        return {}

def _v733_source_summary(engine, cid):
    try:
        ev = _v732_evidence(engine, cid)
    except Exception:
        ev = []
    if not ev:
        return {"source_type":"SOURCE NOT LINKED","group":"","sender":"","sender_phone":"","message_timestamp":"","full_message":""}
    d = (ev[0].get("display") or {})
    return {
        "source_type": d.get("source_type") or (ev[0].get("link") or {}).get("source_type") or "SOURCE",
        "group": d.get("group") or "",
        "sender": d.get("sender") or "",
        "sender_phone": d.get("sender_phone") or "",
        "message_timestamp": d.get("message_timestamp") or "",
        "full_message": d.get("full_message") or "",
    }

def _v733_clean_record(row):
    d = row.get("clean_record") if isinstance(row, dict) else None
    return d if isinstance(d, dict) else {}

def _v733_pick_any(row, names):
    clean = _v733_clean_record(row)
    for src in (row or {}, clean):
        low = {str(k).lower(): k for k in src.keys()}
        for name in names:
            key = low.get(str(name).lower())
            if key is not None:
                v = src.get(key)
                if v not in (None, "", [], {}):
                    return v
    return None

def _v733_display_value(v):
    if v in (None, "", [], {}):
        return ""
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)

def _v733_requirement_fields(r):
    clean = _v733_clean_record(r)
    preferred = [
        ("Client / Contact", ["client_name","contact_name","name"]),
        ("Company / Brand", ["company_name","brand_name","retailer_name","operator_name"]),
        ("Requirement Type", ["requirement_type","demand_type"]),
        ("Property Type", ["property_type","asset_type","category"]),
        ("Use / Suitable Category", ["suitable_category","use_case","intended_use","business_category"]),
        ("City", ["city"]),
        ("Preferred Locations", ["preferred_locations","locations","location","locality"]),
        ("Transaction", ["transaction_type","rent_or_sale","transaction"]),
        ("Area Sq Ft", ["area_sqft","requirement_sqft","minimum_area_sqft","maximum_area_sqft"]),
        ("Minimum Area", ["minimum_area_sqft","min_area_sqft"]),
        ("Maximum Area", ["maximum_area_sqft","max_area_sqft"]),
        ("Sale Budget", ["sale_budget","sale_amount","budget_sale"]),
        ("Rent Budget", ["rent_budget","rent_amount","budget_rent"]),
        ("Floor Preference", ["floor","floor_preference"]),
        ("Frontage", ["frontage","frontage_ft"]),
        ("Parking", ["parking","parking_requirement"]),
        ("Possession / Timeline", ["possession","timeline","required_by","move_in"]),
        ("Nearby Brands", ["nearby_brands"]),
        ("Additional Points", ["additional_points","remarks","notes","requirement_notes"]),
        ("Contact Phone", ["contact_phone","phone","mobile","phones"]),
        ("Contact Email", ["contact_email","email"]),
    ]
    out = []
    used = set()
    low = {str(k).lower(): k for k in clean.keys()}
    for label, names in preferred:
        val = _v733_pick_any(r, names)
        if val not in (None, "", [], {}):
            out.append((label, _v733_display_value(val)))
            for n in names:
                if n.lower() in low:
                    used.add(low[n.lower()])
    for k, v in clean.items():
        if k in used or v in (None, "", [], {}):
            continue
        if len(out) >= 36:
            break
        out.append((str(k).replace("_", " ").title(), _v733_display_value(v)))
    return out

def _v733_requirement_card(r, action=None, source=None):
    action = action or {}
    source = source or {}
    phones = ", ".join(map(str, r.get("phones") or []))
    return f"""<div class='card'>
      <h3>Requirement Intelligence</h3>
      <div class='grid'>
        <div><b>Requirement ID</b><br>{html.escape(str(r.get('canonical_id') or ''))}</div>
        <div><b>Source</b><br>{html.escape(str(source.get('source_type') or ''))}</div>
        <div><b>WhatsApp Group / Chat</b><br>{html.escape(str(source.get('group') or 'Not captured'))}</div>
        <div><b>Sender</b><br>{html.escape(str(source.get('sender') or 'Not captured'))}</div>
        <div><b>Received</b><br>{html.escape(str(source.get('message_timestamp') or 'Not captured'))}</div>
        <div><b>Assigned To</b><br>{html.escape(str(action.get('assigned_to') or 'UNASSIGNED'))}</div>
        <div><b>Location</b><br>{html.escape(str(r.get('locality') or ''))}</div>
        <div><b>Transaction</b><br>{html.escape(str(r.get('transaction_type') or ''))}</div>
        <div><b>Area</b><br>{html.escape(str(r.get('area_sqft_display') or ''))} sq ft</div>
        <div><b>Sale Budget</b><br>{html.escape(str(r.get('sale_budget') or ''))}</div>
        <div><b>Rent Budget</b><br>{html.escape(str(r.get('rent_budget') or ''))}</div>
        <div><b>Internal Requirement Contact</b><br>{html.escape(phones)}</div>
      </div>
    </div>"""

def _v733_property_value(p, names):
    return _v733_pick_any(p, names)

# 7.3.4 UNIVERSAL RECORD STANDARD
# One master entity. Every source/evidence event remains preserved underneath it.
# Original description/message is evidence and must never be replaced by AI structured fields.
def _v734_first_evidence(engine, cid):
    try:
        ev = _v732_evidence(engine, cid)
    except Exception:
        ev = []
    return ev[0] if ev else {}

def _v734_universal_record(engine, cid, row=None, entity_type="RECORD"):
    row = row or {}
    action = _get_action(engine, cid) or {}
    ev = _v734_first_evidence(engine, cid)
    display = ev.get("display") or {}
    link = ev.get("link") or {}

    source_type = display.get("source_type") or link.get("source_type") or "SOURCE NOT LINKED"
    source_name = display.get("group") or _v733_pick_any(
        row, ["source_name","group_name","chat_name","newspaper_name","magazine_name","website","source"]
    ) or ""
    person_name = display.get("sender") or _v733_pick_any(
        row, ["contact_name","sender_name","owner_name","broker_name","client_name","name"]
    ) or ""
    contact = display.get("sender_phone") or _v733_pick_any(
        row, ["contact_phone","phone","mobile","phone_number","owner_phone","broker_phone"]
    ) or ""
    if not contact:
        phones = row.get("phones") if isinstance(row, dict) else None
        if isinstance(phones, (list, tuple)) and phones:
            contact = ", ".join(str(x) for x in phones if x not in (None, ""))
    email = _v733_pick_any(row, ["email","contact_email","sender_email","owner_email","broker_email"]) or ""
    company = _v733_pick_any(row, ["company_name","brand_name","retailer_name","operator_name","business_name"]) or ""
    role = _v733_pick_any(row, ["role","contact_role","sender_role","party_role"]) or ""
    dt = display.get("message_timestamp") or _v733_pick_any(
        row, ["source_datetime","received_at","posted_at","created_at","date_time","datetime"]
    ) or ""
    description = display.get("full_message") or ""
    if not description and "MANUAL" in str(source_type).upper():
        description = _v733_pick_any(
            row, ["original_description","original_message","message","description","raw_text","source_text"]
        ) or ""
    verification = row.get("verification_status") or "UNVERIFIED"
    verified_at = row.get("verified_at") or ""
    verified_by = row.get("verified_by") or ""
    assigned_to = action.get("assigned_to") or row.get("workflow_assigned_to") or ""
    updated_at = row.get("updated_at") or action.get("updated_at") or ""
    created_at = row.get("created_at") or ""
    source_record_id = link.get("source_pk") or ""
    source_table = link.get("source_table") or ""
    return {
        "record_id": cid, "entity_type": entity_type, "date_time": dt,
        "source": source_type, "source_name": source_name, "name": person_name,
        "contact": contact, "email": email, "company": company, "role": role,
        "description": description, "assigned_to": assigned_to,
        "verification_status": verification, "verified_at": verified_at,
        "verified_by": verified_by, "source_record_id": source_record_id,
        "source_table": source_table, "created_at": created_at, "updated_at": updated_at,
    }

def _v734_short(v, n=180):
    s = _v733_display_value(v)
    return s if len(s) <= n else s[:n-1] + "…"

def _v734_universal_header(engine, cid, row=None, entity_type="RECORD"):
    u = _v734_universal_record(engine, cid, row or {}, entity_type)
    desc = u.get("description") or "Original description/message not captured in linked source evidence."
    return f"""<div class='card'>
      <h3>Original Source & Evidence</h3>
      <div class='grid'>
        <div><b>Date & Time</b><br>{html.escape(str(u.get('date_time') or 'Not captured'))}</div>
        <div><b>Source</b><br>{html.escape(str(u.get('source') or 'Not captured'))}</div>
        <div><b>Source Name</b><br>{html.escape(str(u.get('source_name') or 'Not captured'))}</div>
        <div><b>Name</b><br>{html.escape(str(u.get('name') or 'Not captured'))}</div>
        <div><b>Contact No.</b><br>{html.escape(str(u.get('contact') or 'Not captured'))}</div>
        <div><b>Email</b><br>{html.escape(str(u.get('email') or 'Not captured'))}</div>
        <div><b>Company / Brand</b><br>{html.escape(str(u.get('company') or 'Not captured'))}</div>
        <div><b>Role</b><br>{html.escape(str(u.get('role') or 'Not captured'))}</div>
        <div><b>Assigned To</b><br>{html.escape(str(u.get('assigned_to') or 'UNASSIGNED'))}</div>
        <div><b>Verification</b><br>{html.escape(str(u.get('verification_status') or 'UNVERIFIED'))}</div>
        <div><b>Verified Date & Time</b><br>{html.escape(str(u.get('verified_at') or 'Not verified'))}</div>
        <div><b>Verified By</b><br>{html.escape(str(u.get('verified_by') or 'Not verified'))}</div>
        <div><b>Record ID</b><br>{html.escape(str(u.get('record_id') or ''))}</div>
        <div><b>Source Record ID</b><br>{html.escape(str(u.get('source_record_id') or 'Not captured'))}</div>
      </div>
      <h4>Original Description / Message</h4>
      <pre style='font-size:14px;background:#f8fafc;border:1px solid #e1e7ee;padding:14px;border-radius:9px'>{html.escape(str(desc))}</pre>
      <p class='muted'><b>Rule:</b> this text is source evidence. AI structured fields are displayed separately and never overwrite it.</p>
    </div>"""

def _v734_queue_cells(engine, cid, row=None, entity_type="RECORD"):
    u = _v734_universal_record(engine, cid, row or {}, entity_type)
    return {
        "date_time": html.escape(str(u.get("date_time") or "Not captured")),
        "source": html.escape(str(u.get("source") or "Not captured")),
        "source_name": html.escape(str(u.get("source_name") or "Not captured")),
        "name": html.escape(str(u.get("name") or "Not captured")),
        "contact": html.escape(str(u.get("contact") or "Not captured")),
        "description": html.escape(_v734_short(u.get("description") or "Original message not captured", 220)),
        "assigned_to": html.escape(str(u.get("assigned_to") or "UNASSIGNED")),
        "verification": html.escape(str(u.get("verification_status") or "UNVERIFIED")),
    }
def _button(url,label,cls="mini"):
    return f'<a class="{cls}" href="{html.escape(url,quote=True)}">{html.escape(label)}</a>'

def register(core):
    app=_app(core);engine=_engine(core)
    if engine is None: raise RuntimeError("Database engine unavailable")
    import alliance_master_integration_v720 as v720
    import alliance_acceptance_v721 as v721
    # 7.3.1 BOOT FIX: v721 acceptance runs after startup, while 7.3 registers during import.
    # Recover the already-certified PASS from persistent acceptance history when live STATE is not ready.
    cert=(v721.STATE.get("result") or {})
    if cert.get("certification")!="V7_2_OPERATIONAL_ACCEPTANCE_PASS":
        persisted=None
        try:
            with engine.connect() as _c731:
                _row731=_c731.execute(text("""SELECT result FROM pi_acceptance_runs_v721
                  WHERE status='PASS' ORDER BY run_id DESC LIMIT 1""")).mappings().first()
                if _row731:
                    persisted=_safe(_row731.get("result"))
        except Exception:
            persisted=None
        if isinstance(persisted,str):
            try: persisted=json.loads(persisted)
            except Exception: persisted=None
        if isinstance(persisted,dict) and persisted.get("certification")=="V7_2_OPERATIONAL_ACCEPTANCE_PASS":
            cert=persisted
        else:
            raise RuntimeError("7.2.1 certified PASS not found in memory or persisted acceptance history")
    with engine.begin() as c:
        for ddl in DDL:c.execute(text(ddl))

    @app.get("/alliance/primary",response_class=HTMLResponse)
    def primary(req:Request):
        _role(core,req);c=_counts(engine)
        cards="".join(f"<div class='card'><div class='muted'>{html.escape(k.replace('_',' ').title())}</div><div class='num'>{v}</div></div>" for k,v in c.items())
        body=f"""<div class='grid'>{cards}</div>
        <div class='card'><h3>Alliance Universal Record Standard</h3><p><b>Every master record:</b> Date & Time · Source · Source Name · Name · Contact No. · Original Description/Message · Assignment · Verification · permanent Record ID · source evidence lineage. AI extraction is separate and never overwrites the original message.</p></div><div class='card'><h3>Daily Operating Flow</h3><p><b>1.</b> Open Properties and verify availability. <b>2.</b> Open Requirements and run Match.
        <b>3.</b> Review exact and alternative options. <b>4.</b> Approve only suitable verified properties.
        <b>5.</b> Generate client-safe draft. <b>6.</b> Assign follow-up.</p></div>
        <div class='grid'>
        <div class='card'><h3>Property Team</h3><p>Search canonical inventory, view internal contact/evidence, verify availability and assign responsibility.</p>{_button('/alliance/primary/properties','Open Properties','btn good')}</div>
        <div class='card'><h3>Leasing Team</h3><p>Open requirements, run full 3,507-property matching and review fallback alternatives if exact locality is unavailable.</p>{_button('/alliance/primary/requirements','Open Requirements','btn')}</div>
        <div class='card'><h3>Follow-up</h3><p>One queue for assigned work and scheduled follow-ups.</p>{_button('/alliance/primary/followups','Open Follow-ups','btn alt')}</div></div>"""
        return HTMLResponse(_shell(core,req,"Primary Command Centre",body))

    @app.get("/alliance/primary/properties",response_class=HTMLResponse)
    def properties(req:Request,q:str=Query(""),transaction:str=Query(""),verification:str=Query("")):
        _role(core,req);rows=v720._search_properties(engine,"",transaction,1000)
        if verification: rows=[x for x in rows if x.get("verification_status")==verification.upper()]
        form=f"""<form class='inline'><input name='q' value='{html.escape(q,quote=True)}' placeholder='Search message, name, contact, source, locality'>
        <select name='transaction'><option value=''>All transactions</option><option {'selected' if transaction=='SALE' else ''}>SALE</option><option {'selected' if transaction=='RENT' else ''}>RENT</option></select>
        <select name='verification'><option value=''>All verification</option><option {'selected' if verification=='VERIFIED' else ''}>VERIFIED</option><option {'selected' if verification=='UNVERIFIED' else ''}>UNVERIFIED</option></select>
        <button class='btn'>Search</button></form>"""
        trs=[]
        ql=q.strip().lower()
        for p in rows:
            cid=p["canonical_id"];u=_v734_queue_cells(engine,cid,p,"PROPERTY")
            if ql:
                blob=" ".join([u["date_time"],u["source"],u["source_name"],u["name"],u["contact"],u["description"],str(p.get("locality") or "")]).lower()
                if ql not in blob: continue
            actions=f"<div class='actions'>{_button('/alliance/primary/property/'+cid,'Open')}"
            if p.get("verification_status")!="VERIFIED":
                actions+=f"""<form method='post' action='/alliance/primary/property/{html.escape(cid,quote=True)}/verify' style='display:inline'><button class='mini good'>Verify</button></form>"""
            actions+="</div>"
            trs.append(f"""<tr>
              <td>{actions}</td><td>{u['date_time']}</td><td>{u['source']}</td><td>{u['source_name']}</td>
              <td>{u['name']}</td><td>{u['contact']}</td><td style='min-width:300px'>{u['description']}</td>
              <td>{html.escape(str(_v733_pick_any(p,["address","property_address","unit_address","building_address"]) or "Not captured"))}</td>
              <td>{html.escape(str(p.get('locality') or ''))}</td><td>{html.escape(str(p.get('transaction_type') or ''))}</td>
              <td>{html.escape(str(p.get('area_sqft_display') or ''))}</td><td>{html.escape(str(p.get('area_sqyd') or ''))}</td><td>{html.escape(str(p.get('area_sqm') or ''))}</td>
              <td>{html.escape(str(p.get('sale_amount') or ''))}</td><td>{html.escape(str(p.get('rent_amount') or ''))}</td>
              <td>{u['verification']}</td><td>{u['assigned_to']}</td></tr>""")
        table=f"""<div class='card'><p><b>Universal record rule:</b> Date/time, source, source name, name, contact and original description remain visible with every master record. Original evidence is never replaced by AI interpretation.</p></div>
        <div class='card tablebox'><table><tr><th>Actions</th><th>Date & Time</th><th>Source</th><th>Source Name</th><th>Name</th><th>Contact No.</th><th>Original Description / Message</th>
        <th>Address</th><th>Locality</th><th>Transaction</th><th>Sq Ft</th><th>Sq Yd</th><th>Sq M</th><th>Sale Amount</th><th>Rent Amount</th><th>Verification</th><th>Assigned To</th></tr>{''.join(trs)}</table></div>"""
        return HTMLResponse(_shell(core,req,f"Master Properties · {len(trs)} shown",form+table))

    @app.get("/alliance/primary/property/{cid}",response_class=HTMLResponse)
    def property_detail(cid:str,req:Request):
        _role(core,req);p=_property(engine,cid)
        if not p:raise HTTPException(404,"Property not found")
        action=_get_action(engine,cid);links=_source_links(engine,cid);logs=_logs(engine,cid)
        phones=", ".join(map(str,p.get("phones") or []))
        source_html="".join(f"<tr><td>{html.escape(str(x['source_type']))}</td><td>{html.escape(str(x['source_table']))}</td><td>{html.escape(str(x['source_pk']))}</td><td>{html.escape(str(x['source_row_hash']))}</td></tr>" for x in links)
        log_html="".join(f"<tr><td>{html.escape(str(x['created_at']))}</td><td>{html.escape(str(x['action']))}</td><td>{html.escape(str(x['actor'] or ''))}</td><td>{html.escape(json.dumps(x['details'],ensure_ascii=False) if isinstance(x['details'],dict) else str(x['details']))}</td></tr>" for x in logs)
        body=_v734_universal_header(engine,cid,p,"PROPERTY")+f"""<div class='grid'>
        <div class='card'><h3>{html.escape(str(p.get('locality') or cid))}</h3>
        <p><b>ID:</b> {html.escape(cid)}<br><b>Address:</b> {html.escape(str(_v733_pick_any(p,["address","property_address","unit_address","building_address"]) or "Not captured"))}<br><b>Transaction:</b> {html.escape(str(p.get('transaction_type') or ''))}<br>
        <b>Area:</b> {p.get('area_sqft_display') or ''} sq ft · {p.get('area_sqyd') or ''} sq yd · {p.get('area_sqm') or ''} sq m · {p.get('area_acre') or ''} acre<br>
        <b>Sale:</b> {html.escape(str(p.get('sale_amount') or ''))}<br><b>Rent:</b> {html.escape(str(p.get('rent_amount') or ''))}<br>
        <b>Internal contact:</b> {html.escape(phones)}<br><b>Verification:</b> {html.escape(str(p.get('verification_status') or ''))}<br>
        <b>Availability:</b> {html.escape(str(p.get('availability_status') or 'UNKNOWN'))}</p>
        <div class='actions'><form method='post' action='/alliance/primary/property/{html.escape(cid,quote=True)}/verify'><button class='btn good'>Verify Available Today</button></form>
        <form method='post' action='/alliance/primary/property/{html.escape(cid,quote=True)}/unavailable'><button class='btn warn'>Mark Unavailable</button></form></div></div>
        <div class='card'><h3>Assignment & Follow-up</h3><form method='post' action='/alliance/primary/action/{html.escape(cid,quote=True)}/PROPERTY'>
        <label>Assigned To</label><br><input name='assigned_to' value='{html.escape(str(action.get("assigned_to") or ""),quote=True)}'><br><br>
        <label>Stage</label><br><select name='stage'>{''.join(f"<option {'selected' if action.get('stage')==x else ''}>{x}</option>" for x in ['NEW','VERIFIED','MATCHED','REVIEW','CONTACTED','FOLLOW_UP','SITE_VISIT','CLOSED','UNAVAILABLE'])}</select><br><br>
        <label>Next Follow-up</label><br><input name='next_followup_at' type='datetime-local'><br><br>
        <label>Internal Notes</label><br><textarea name='internal_notes' rows='4'>{html.escape(str(action.get("internal_notes") or ""))}</textarea><br>
        <button class='btn'>Save Workflow</button></form></div></div>
        {_v732_evidence_html(engine,cid)}
        <div class='card'><h3>Source Evidence Links</h3><div class='tablebox'><table><tr><th>Source</th><th>Table</th><th>Source PK</th><th>Evidence Hash</th></tr>{source_html}</table></div></div>
        <div class='card'><h3>Canonical Record</h3><pre>{html.escape(json.dumps(p.get('clean_record') or {},ensure_ascii=False,indent=2))}</pre></div>
        <div class='card'><h3>Action History</h3><table><tr><th>Time</th><th>Action</th><th>Actor</th><th>Detail</th></tr>{log_html}</table></div>"""
        return HTMLResponse(_shell(core,req,"Property Detail",body))

    @app.post("/alliance/primary/property/{cid}/verify")
    def property_verify(cid:str,req:Request):
        _role(core,req);_verify_property(engine,cid,_actor(core,req))
        return RedirectResponse("/alliance/primary/property/"+cid,status_code=303)

    @app.post("/alliance/primary/property/{cid}/unavailable")
    def property_unavailable(cid:str,req:Request):
        _role(core,req);_mark_unavailable(engine,cid,_actor(core,req))
        return RedirectResponse("/alliance/primary/property/"+cid,status_code=303)

    @app.post("/alliance/primary/action/{cid}/{etype}")
    def action_update(cid:str,etype:str,req:Request,assigned_to:str=Form(""),stage:str=Form("NEW"),
                      next_followup_at:str=Form(""),internal_notes:str=Form("")):
        _role(core,req);et=etype.upper()
        if et not in {"PROPERTY","REQUIREMENT"}:raise HTTPException(400,"Invalid entity type")
        nf=next_followup_at or None
        _set_action(engine,cid,et,_actor(core,req),assigned_to=assigned_to or None,stage=stage,
                    next_followup_at=nf,followup_status="SCHEDULED" if nf else "NOT_SCHEDULED",
                    internal_notes=internal_notes or None)
        target="/alliance/primary/property/"+cid if et=="PROPERTY" else "/alliance/primary/requirement/"+cid
        return RedirectResponse(target,status_code=303)

    @app.get("/alliance/primary/requirements",response_class=HTMLResponse)
    def requirements(req:Request,q:str=Query(""),transaction:str=Query(""),source:str=Query(""),assignment:str=Query("")):
        _role(core,req)
        rows=v720._search_requirements(engine,"",transaction,500)
        actions=_v733_action_map(engine,"REQUIREMENT")
        enriched=[];ql=q.strip().lower()
        for r in rows:
            cid=r["canonical_id"];act=actions.get(cid) or {};u=_v734_queue_cells(engine,cid,r,"REQUIREMENT")
            source_text=" ".join([u["source"],u["source_name"],u["name"],u["contact"],u["description"]]).lower()
            if source and source.lower() not in source_text: continue
            if assignment=="UNASSIGNED" and act.get("assigned_to"): continue
            if assignment=="ASSIGNED" and not act.get("assigned_to"): continue
            if ql:
                blob=" ".join([source_text,str(r.get("locality") or ""),str(r.get("transaction_type") or ""),str(r.get("sale_budget") or ""),str(r.get("rent_budget") or "")]).lower()
                if ql not in blob: continue
            enriched.append((r,act,u))
        form=f"""<div class='card'><form class='inline'>
        <input name='q' value='{html.escape(q,quote=True)}' placeholder='Search message, name, contact, source, location'>
        <select name='transaction'><option value=''>All transactions</option><option {'selected' if transaction=='SALE' else ''}>SALE</option><option {'selected' if transaction=='RENT' else ''}>RENT</option></select>
        <input name='source' value='{html.escape(source,quote=True)}' placeholder='Source / WhatsApp group'>
        <select name='assignment'><option value=''>All assignments</option><option value='UNASSIGNED' {'selected' if assignment=='UNASSIGNED' else ''}>UNASSIGNED</option><option value='ASSIGNED' {'selected' if assignment=='ASSIGNED' else ''}>ASSIGNED</option></select>
        <button class='btn'>Search</button></form></div>"""
        trs=[]
        for r,act,u in enriched:
            cid=r["canonical_id"]
            acts=_button("/alliance/primary/requirement/"+cid,"Full Requirement")+" "+_button("/alliance/primary/availability?requirement_id="+cid,"Availability","mini good")
            stage=str(act.get("stage") or "NEW")
            assign=f"""<form method='post' action='/alliance/primary/action/{html.escape(cid,quote=True)}/REQUIREMENT'>
              <input name='assigned_to' value='{html.escape(str(act.get("assigned_to") or ""),quote=True)}' placeholder='Team member' style='width:125px'>
              <input type='hidden' name='stage' value='{html.escape(stage,quote=True)}'><input type='hidden' name='next_followup_at' value=''>
              <input type='hidden' name='internal_notes' value='{html.escape(str(act.get("internal_notes") or ""),quote=True)}'>
              <button class='mini'>Assign</button></form><small>{html.escape(stage)}</small>"""
            trs.append(f"""<tr><td>{acts}</td><td>{u['date_time']}</td><td>{u['source']}</td><td>{u['source_name']}</td>
              <td>{u['name']}</td><td>{u['contact']}</td><td style='min-width:340px'>{u['description']}</td>
              <td>{html.escape(str(r.get('locality') or ''))}</td><td>{html.escape(str(r.get('transaction_type') or ''))}</td>
              <td>{html.escape(str(r.get('area_sqft_display') or ''))}</td><td>{html.escape(str(r.get('sale_budget') or ''))}</td><td>{html.escape(str(r.get('rent_budget') or ''))}</td>
              <td>{u['verification']}</td><td>{assign}</td></tr>""")
        table=f"""<div class='card'><p><b>Universal record rule:</b> Requirement Message is the actual original source message when recoverable. AI extracted fields stay separate.</p></div>
        <div class='card tablebox'><table><tr><th>Actions</th><th>Date & Time</th><th>Source</th><th>Source Name</th><th>Name</th><th>Contact No.</th><th>Original Requirement Message</th>
        <th>Location</th><th>Transaction</th><th>Sq Ft</th><th>Sale Budget</th><th>Rent Budget</th><th>Verification</th><th>Team Assignment</th></tr>{''.join(trs)}</table></div>"""
        return HTMLResponse(_shell(core,req,f"Requirement Intelligence · {len(enriched)}",form+table))

    @app.get("/alliance/primary/requirement/{cid}",response_class=HTMLResponse)
    def requirement_detail(cid:str,req:Request):
        _role(core,req)
        r=_requirement(engine,cid)
        if not r:raise HTTPException(404,"Requirement not found")
        action=_get_action(engine,cid)
        source=_v733_source_summary(engine,cid)
        fields=_v733_requirement_fields(r)
        field_html="".join(f"<div><b>{html.escape(str(k))}</b><br>{html.escape(str(v))}</div>" for k,v in fields)
        body=_v734_universal_header(engine,cid,r,"REQUIREMENT")+_v733_requirement_card(r,action,source)
        body+=f"""<div class='grid'>
        <div class='card'><h3>Team Ownership & Next Action</h3>
        <form method='post' action='/alliance/primary/action/{html.escape(cid,quote=True)}/REQUIREMENT'>
        <label>Assigned To</label><br><input name='assigned_to' placeholder='Team member' value='{html.escape(str(action.get("assigned_to") or ""),quote=True)}'><br><br>
        <label>Stage</label><br><select name='stage'>{''.join(f"<option {'selected' if action.get('stage')==x else ''}>{x}</option>" for x in ['NEW','VERIFY_REQUIREMENT','MATCHING','AVAILABILITY_CHECK','MATCHED','REVIEW','CONTACTED','FOLLOW_UP','SITE_VISIT','NEGOTIATION','CLOSED'])}</select><br><br>
        <label>Next Follow-up</label><br><input name='next_followup_at' type='datetime-local'><br><br>
        <label>Internal Notes</label><br><textarea name='internal_notes' rows='5'>{html.escape(str(action.get("internal_notes") or ""))}</textarea><br>
        <button class='btn'>Save Workflow</button></form></div>
        <div class='card'><h3>Requirement Actions</h3>
        <p>Use Availability Verification before approving any property for client sharing.</p>
        {_button('/alliance/primary/availability?requirement_id='+cid,'Open Availability Verification','btn good')}
        {_button('/alliance/primary/matcher?requirement_id='+cid,'Open Matcher','btn alt')}</div></div>
        <div class='card'><h3>All Captured Requirement Details</h3><div class='grid'>{field_html or '<div>No structured fields captured beyond the canonical summary.</div>'}</div></div>
        {_v732_evidence_html(engine,cid)}
        <div class='card'><h3>Canonical Requirement Record</h3><pre>{html.escape(json.dumps(r.get('clean_record') or {},ensure_ascii=False,indent=2))}</pre></div>"""
        return HTMLResponse(_shell(core,req,"Full Requirement Detail",body))

    @app.get("/alliance/primary/availability",response_class=HTMLResponse)
    def availability(req:Request,requirement_id:str=Query(""),tier:str=Query(""),verified_only:str=Query("")):
        _role(core,req)
        reqs=v720._search_requirements(engine,limit=300)
        options="".join(f"<option value='{html.escape(x['canonical_id'],quote=True)}' {'selected' if requirement_id==x['canonical_id'] else ''}>{html.escape((x.get('locality') or 'Requirement')+' · '+x['canonical_id'])}</option>" for x in reqs)
        form=f"""<div class='card'><form class='inline'>
          <select name='requirement_id'><option value=''>Choose requirement</option>{options}</select>
          <select name='tier'><option value=''>All match tiers</option><option value='EXACT_LOCALITY' {'selected' if tier=='EXACT_LOCALITY' else ''}>EXACT LOCALITY</option><option value='SAME_CITY_ALTERNATIVE' {'selected' if tier=='SAME_CITY_ALTERNATIVE' else ''}>SAME CITY ALTERNATIVE</option><option value='TRANSACTION_AREA_ALTERNATIVE' {'selected' if tier=='TRANSACTION_AREA_ALTERNATIVE' else ''}>BROADER ALTERNATIVE</option></select>
          <label><input type='checkbox' name='verified_only' value='1' {'checked' if verified_only else ''}> Verified + available only</label>
          <button class='btn good'>Load Availability</button>
        </form></div>"""
        if not requirement_id:
            body=form+"""<div class='card'><h3>Availability Verification Workspace</h3>
            <p>Select a requirement. The screen checks the full master inventory, keeps exact-locality and alternative tiers separate, and shows internal verification contacts to the team.</p>
            <p><b>Confirmed availability rule:</b> only properties marked VERIFIED and AVAILABLE are treated as confirmed. Unverified stock remains visible for verification but is not client-ready.</p></div>"""
            return HTMLResponse(_shell(core,req,"Availability Verification",body))
        rr,matches=_match_full(engine,requirement_id,120)
        action=_get_action(engine,requirement_id)
        source=_v733_source_summary(engine,requirement_id)
        rows=[]
        for m in matches:
            if tier and m.get("tier")!=tier:
                continue
            p=m["property"]
            if verified_only and not (p.get("verification_status")=="VERIFIED" and p.get("availability_status")=="AVAILABLE"):
                continue
            cid=p["canonical_id"]
            phones=", ".join(map(str,p.get("phones") or []))
            ptype=_v733_display_value(_v733_property_value(p,["property_type","asset_type","category"]))
            floor=_v733_display_value(_v733_property_value(p,["floor","floor_no","floor_number"]))
            frontage=_v733_display_value(_v733_property_value(p,["frontage","frontage_ft"]))
            parking=_v733_display_value(_v733_property_value(p,["parking"]))
            possession=_v733_display_value(_v733_property_value(p,["possession","possession_status","available_from"]))
            cam=_v733_display_value(_v733_property_value(p,["cam","cam_per_sqft","maintenance"]))
            deposit=_v733_display_value(_v733_property_value(p,["security_deposit","deposit"]))
            verification=str(p.get("verification_status") or "UNVERIFIED")
            avail=str(p.get("availability_status") or "UNKNOWN")
            confirmed=verification=="VERIFIED" and avail=="AVAILABLE"
            status="<span class='ok'>CONFIRMED AVAILABLE</span>" if confirmed else f"<span class='warntext'>{html.escape(verification)} · {html.escape(avail)}</span>"
            verify_action=""
            if not confirmed:
                verify_action=f"""<form method='post' action='/alliance/primary/property/{html.escape(cid,quote=True)}/verify' style='display:inline'><button class='mini good'>Verify Available</button></form>"""
            why=m.get("reasons") or []
            why_text=", ".join(str(x) for x in why) if isinstance(why,(list,tuple)) else str(why)
            physical=" · ".join(x for x in [("Floor "+floor) if floor else "",("Frontage "+frontage) if frontage else "",("Parking "+parking) if parking else ""] if x)
            charges=" · ".join(x for x in [("Rent "+str(p.get('rent_amount'))) if p.get('rent_amount') else "",("CAM "+cam) if cam else "",("Deposit "+deposit) if deposit else ""] if x)
            rows.append(f"""<tr>
              <td><b>{m.get('score')}</b><br><span class='pill'>{html.escape(str(m.get('tier') or ''))}</span></td>
              <td>{_button('/alliance/primary/property/'+cid,'Open Property')} {verify_action}</td>
              <td><b>{html.escape(str(p.get('locality') or ''))}</b><br>{html.escape(ptype)}</td>
              <td>{html.escape(str(p.get('area_sqft_display') or ''))}</td>
              <td>{html.escape(charges)}</td>
              <td>{html.escape(str(p.get('sale_amount') or ''))}</td>
              <td>{html.escape(physical)}</td>
              <td>{html.escape(possession)}</td>
              <td>{status}</td>
              <td>{html.escape(phones)}</td>
              <td>{html.escape(why_text)}</td>
            </tr>""")
        summary=_v733_requirement_card(rr,action,source)
        rule="""<div class='card'><b>Availability rule:</b> VERIFIED + AVAILABLE = confirmed stock. UNVERIFIED or UNKNOWN = team must call/check before client sharing. Alternatives remain explicitly labelled and are never presented as exact-locality matches.</div>"""
        table=f"""<div class='card tablebox'><table><tr><th>Score / Tier</th><th>Action</th><th>Property / Location</th><th>Sq Ft</th><th>Rent / Charges</th><th>Sale</th><th>Physical Details</th><th>Possession</th><th>Availability</th><th>Internal Verification Contact</th><th>Why Matched</th></tr>{''.join(rows)}</table></div>"""
        return HTMLResponse(_shell(core,req,f"Availability Verification · {len(rows)} options",form+summary+rule+table))

    @app.get("/alliance/primary/matcher",response_class=HTMLResponse)
    def matcher(req:Request,requirement_id:str=Query("")):
        _role(core,req)
        reqs=v720._search_requirements(engine,limit=100)
        options="".join(f"<option value='{html.escape(x['canonical_id'],quote=True)}' {'selected' if requirement_id==x['canonical_id'] else ''}>{html.escape((x.get('locality') or 'Requirement')+' · '+x['canonical_id'])}</option>" for x in reqs)
        form=f"<form class='inline'><select name='requirement_id'><option value=''>Choose requirement</option>{options}</select><button class='btn good'>Run Full Master Match</button></form>"
        if not requirement_id:return HTMLResponse(_shell(core,req,"AI Property Matcher",form+"<div class='card'>The matcher searches the complete Master inventory. Exact locality is ranked first; if unavailable it transparently shows same-city and transaction/area alternatives.</div>"))
        rr,matches=_match_full(engine,requirement_id,50)
        trs=[]
        for m in matches:
            p=m["property"];cid=p["canonical_id"]
            with engine.connect() as c:
                rv=c.execute(text("""SELECT review_status FROM pi_match_reviews_v730 WHERE requirement_canonical_id=:r AND property_canonical_id=:p"""),{"r":requirement_id,"p":cid}).scalar() or "READY_FOR_REVIEW"
            approve=f"""<form method='post' action='/alliance/primary/match-review' style='display:inline'>
            <input type='hidden' name='requirement_id' value='{html.escape(requirement_id,quote=True)}'><input type='hidden' name='property_id' value='{html.escape(cid,quote=True)}'>
            <button class='mini good' name='decision' value='APPROVED'>Approve</button><button class='mini alt' name='decision' value='REJECTED'>Reject</button></form>"""
            trs.append(f"<tr><td>{m['score']}</td><td>{html.escape(m['tier'])}</td><td>{_button('/alliance/primary/property/'+cid,'Open')}</td><td>{html.escape(str(p.get('locality') or ''))}</td><td>{html.escape(str(p.get('area_sqft_display') or ''))}</td><td>{html.escape(str(p.get('sale_amount') or ''))}</td><td>{html.escape(str(p.get('rent_amount') or ''))}</td><td>{html.escape(str(p.get('verification_status') or ''))}</td><td>{html.escape(str(rv))}<br>{approve}</td></tr>")
        body=form+f"<div class='card'><b>Requirement:</b> {html.escape(str(rr.get('locality') or requirement_id))} · {html.escape(str(rr.get('transaction_type') or ''))} · {rr.get('area_sqft_display') or ''} sq ft<br><small>Tier is explicit. Alternatives are never presented as exact-locality matches.</small></div>"
        body+=f"<div class='card tablebox'><table><tr><th>Score</th><th>Tier</th><th>Property</th><th>Locality</th><th>Sq Ft</th><th>Sale</th><th>Rent</th><th>Verification</th><th>Review</th></tr>{''.join(trs)}</table></div>"
        body+=_button("/alliance/primary/client-draft/"+requirement_id,"Generate Client-Safe Draft","btn good")
        return HTMLResponse(_shell(core,req,f"Matcher · {len(matches)} options",body))

    @app.post("/alliance/primary/match-review")
    def match_review(req:Request,requirement_id:str=Form(...),property_id:str=Form(...),decision:str=Form(...)):
        _role(core,req);d=decision.upper()
        if d not in {"APPROVED","REJECTED"}:raise HTTPException(400,"Invalid decision")
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_match_reviews_v730(requirement_canonical_id,property_canonical_id,review_status,reviewed_by,reviewed_at,updated_at)
              VALUES(:r,:p,:d,:by,NOW(),NOW()) ON CONFLICT(requirement_canonical_id,property_canonical_id) DO UPDATE SET
              review_status=:d,reviewed_by=:by,reviewed_at=NOW(),updated_at=NOW()"""),
              {"r":requirement_id,"p":property_id,"d":d,"by":_actor(core,req)})
        _audit_log(engine,requirement_id,"REQUIREMENT","MATCH_"+d,_actor(core,req),{"property_id":property_id})
        return RedirectResponse("/alliance/primary/matcher?requirement_id="+requirement_id,status_code=303)

    @app.get("/alliance/primary/client-draft/{rid}",response_class=HTMLResponse)
    def client_draft(rid:str,req:Request):
        _role(core,req);r=_requirement(engine,rid)
        if not r:raise HTTPException(404,"Requirement not found")
        props=_approved_matches(engine,rid);msg=_draft(r,props)
        # Hard privacy assertion against every internal phone in selected options.
        leaked=[]
        for p in props:
            for ph in p.get("phones") or []:
                if str(ph) and str(ph) in msg:leaked.append(str(ph))
        if leaked:raise RuntimeError("Privacy gate blocked draft due to contact leakage")
        body=f"""<div class='card'><h3>Client-Safe WhatsApp Draft</h3><p class='muted'>Only APPROVED + VERIFIED + AVAILABLE properties are included. Owner/broker/source contact numbers are never included.</p>
        <textarea id='draft' rows='14' style='width:100%' readonly>{html.escape(msg)}</textarea><br><button class='btn good' onclick="navigator.clipboard.writeText(document.getElementById('draft').value)">Copy Draft</button>
        <p><b>Eligible approved options:</b> {len(props)}</p></div>"""
        return HTMLResponse(_shell(core,req,"Client-Safe Draft",body))

    @app.get("/alliance/primary/followups",response_class=HTMLResponse)
    def followups(req:Request):
        _role(core,req)
        with engine.connect() as c:
            rows=_rows(c.execute(text("""SELECT * FROM pi_master_action_state_v730
              WHERE followup_status='SCHEDULED' OR assigned_to IS NOT NULL ORDER BY next_followup_at NULLS LAST,updated_at DESC LIMIT 500""")))
        trs=[]
        for x in rows:
            url=("/alliance/primary/property/" if x["entity_type"]=="PROPERTY" else "/alliance/primary/requirement/")+x["canonical_id"]
            trs.append(f"<tr><td>{_button(url,'Open')}</td><td>{html.escape(str(x['entity_type']))}</td><td>{html.escape(str(x.get('assigned_to') or ''))}</td><td>{html.escape(str(x.get('stage') or ''))}</td><td>{html.escape(str(x.get('next_followup_at') or ''))}</td><td>{html.escape(str(x.get('followup_status') or ''))}</td></tr>")
        return HTMLResponse(_shell(core,req,f"Follow-up Queue · {len(rows)}",f"<div class='card tablebox'><table><tr><th>Open</th><th>Type</th><th>Assigned</th><th>Stage</th><th>Next Follow-up</th><th>Status</th></tr>{''.join(trs)}</table></div>"))

    @app.get("/api/v7.3/health")
    def health(req:Request):
        _role(core,req)
        paths=["/alliance/primary","/alliance/primary/properties","/alliance/primary/requirements","/alliance/primary/availability","/alliance/primary/matcher","/alliance/primary/followups","/alliance/primary/client-draft/{rid}"]
        return {"status":"ok","version":VERSION,"counts":_counts(engine),"routes":{p:_route_exists(app,p) for p in paths},
                "parent_certification":cert.get("certification"),
                "safety":{"canonical_master_mutations":0,"raw_source_mutations":0,"gold_mutations":0,"champion_mutations":0}}

    STATE.update(status="READY",result={"version":VERSION,"counts":_counts(engine),"primary":"/alliance/primary",
                                      "parent_certification":cert.get("certification")})
    return STATE

def start(core):
    try:return register(core)
    except Exception as exc:
        STATE.update(status="ERROR",last_error=f"{type(exc).__name__}: {exc}")
        return STATE


# 7.3.5 MAGAZINE LOSSLESS EXTRACTION TRAINING


# 7.3.6 MAGAZINE SECTION CONTEXT TRAINING
