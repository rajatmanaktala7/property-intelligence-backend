from __future__ import annotations

import html, json, re
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="1.0.0-COMPACT-EDIT-SENDER-RECOVERY"

def _e(v): return html.escape("" if v is None else str(v))
def _d(v):
    if isinstance(v,dict): return dict(v)
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}
def _phone(v):
    s=str(v or ""); m=re.search(r"(?<!\d)(?:\+?91[\s.-]?)?([6-9](?:[\s.-]?\d){9})(?!\d)",s)
    return re.sub(r"\D","",m.group(1)) if m else ""
def _opaque(v):
    s=str(v or "").strip(); local=s.split("@",1)[0]; domain=s.split("@",1)[1].lower() if "@" in s else ""; digits=re.sub(r"\D","",local)
    if domain in ("s.whatsapp.net","c.us"):return ""
    if domain=="lid":return local
    return digits if len(digits)>=13 else ""

def _registry_phone(engine,*values):
    ids=[]
    for v in values:
        if isinstance(v,dict):
            for k in ("sender_jid","remote_jid","participant","author","sender_id","lid","participant_id","author_id"):
                o=_opaque(v.get(k));
                if o:ids.append(o)
        else:
            o=_opaque(v)
            if o:ids.append(o)
    if not ids:return ""
    try:
        with engine.connect() as c:
            for oid in ids:
                p=c.execute(text("SELECT resolved_phone FROM pi_whatsapp_sender_identity_registry_v1 WHERE opaque_id=:o AND resolution_status='RESOLVED_UNIQUE_EXACT_EVIDENCE' LIMIT 1"),{"o":oid}).scalar()
                if _phone(p):return _phone(p)
    except Exception:pass
    return ""

def _install_phone_recovery(fix,engine):
    original=fix._sender_from_whatsapp
    def sender(row):
        p=original(row)
        if p:return p
        p=_registry_phone(engine,row)
        if p:return p
        try:
            import whatsapp_live_bridge as live
            we=live.wa_engine
            if we is None:return ""
            mid=str(fix._first(row,["message_id","wa_message_id","external_message_id"]) or "")
            rid=str(fix._first(row,["wa_requirement_id","source_pk","requirement_id","id"]) or "")
            with we.connect() as c:
                req=None
                if rid:
                    req=c.execute(text("SELECT to_jsonb(x) FROM wa_requirements x WHERE CAST(wa_requirement_id AS TEXT)=:r OR CAST(id AS TEXT)=:r LIMIT 1"),{"r":rid}).scalar()
                    if isinstance(req,dict):
                        for k in ("contact_phone","sender_phone","sender_jid","remote_jid","participant"):
                            if _phone(req.get(k)):return _phone(req.get(k))
                        p=_registry_phone(engine,req)
                        if p:return p
                        mid=mid or str(req.get("message_id") or "")
                if mid:
                    msg=c.execute(text("SELECT to_jsonb(x) FROM wa_messages x WHERE CAST(message_id AS TEXT)=:m LIMIT 1"),{"m":mid}).scalar()
                    if isinstance(msg,dict):
                        for k in ("sender_phone","phone_number","sender_number","author_phone","contact_phone"):
                            if _phone(msg.get(k)):return _phone(msg.get(k))
                        p=_registry_phone(engine,msg)
                        if p:return p
        except Exception:pass
        return ""
    fix._sender_from_whatsapp=sender


def _install_compact_page(fix):
    original=fix._page
    def page(title,body):
        out=original(title,body)
        css="""<style>
.wrap{max-width:100%;padding:6px}.tablebox{max-height:82vh}table{font-size:9px;font-weight:650;table-layout:auto}th,td{padding:3px 4px;line-height:1.12;max-width:145px;overflow-wrap:anywhere}th{font-size:9px;white-space:nowrap}.desc{min-width:190px;max-width:300px}.loc{min-width:70px;max-width:115px}.date{min-width:88px;max-width:105px}a.btn,button{padding:5px 6px;font-size:9px}.card{padding:6px;margin-bottom:5px}h2{font-size:17px;margin:4px 0 6px}</style>"""
        return out.replace("</head>",css+"</head>")
    fix._page=page


def _install_manual_action(fix):
    original=fix._action
    def action(r,source):
        base=original(r,source)
        src=str(r.get("source") or source).upper(); sid=str(r.get("source_id") or "")
        if src=="MANUAL" and sid:
            base=f"<a class='btn' href='/alliance/final/requirements/edit-manual?source_id={_e(sid)}'>Edit</a> "+base
        return base
    fix._action=action


def _manual_row(engine,source_id):
    try:
        with engine.connect() as c:
            rows=c.execute(text("SELECT to_jsonb(g) FROM pi_requirement_gate_v1191 g WHERE source_type='MANUAL' ORDER BY id DESC LIMIT 10000")).scalars().all()
        for raw in rows:
            d=_d(raw)
            if str(d.get("source_pk") or d.get("id") or "")==str(source_id):return d
    except Exception:pass
    return None

def _val(d,key,default=""):
    ex=_d(d.get("extracted_fields")); v=d.get(key)
    return v if v not in (None,"",[],{}) else ex.get(key,default)

def _edit_page(fix,row):
    ex=_d(row.get("extracted_fields")); loc=row.get("locations") or ex.get("locations") or []
    if isinstance(loc,list):loc=", ".join(str(x) for x in loc)
    phones=row.get("contact_numbers") or ex.get("contact_numbers") or []
    if isinstance(phones,list):phones=", ".join(str(x) for x in phones)
    fields=[("Requirement / Description","message",row.get("original_message")),("Client / Company","company",ex.get("company_brand_person")),("Contact Name","contact_name",ex.get("contact_name")),("Contact No.","contact",phones),("Location","location",loc),("Category / Purpose","category",row.get("intended_use") or ex.get("intended_use")),("Property Type","property_type",row.get("property_category") or ex.get("property_category")),("Area Min","area_min",row.get("area_min_sqft")),("Area Max","area_max",row.get("area_max_sqft")),("Rent / Sale","transaction",row.get("transaction_type")),("Budget","budget",row.get("budget_max")),("Floor / Preference","floor",row.get("floor_requirement"))]
    inputs="".join(f"<label><b>{_e(label)}</b></label><br><input style='width:100%;max-width:760px' name='{name}' value='{_e(value)}'><br><br>" for label,name,value in fields)
    body=f"<div class=card><form method='post' action='/alliance/final/requirements/edit-manual'><input type=hidden name=source_id value='{_e(row.get('source_pk') or row.get('id'))}'>{inputs}<button type=submit>Save Changes</button></form></div>"
    return fix._page("Edit Manual Requirement",body)

def register(core,requirement_app=None,served_app=None):
    import alliance_ui_data_rectification_v1 as fix
    engine=getattr(core,"engine",None); app=requirement_app or served_app or getattr(core,"app",None) or core
    if engine is None:return {"status":"NO_ENGINE","version":VERSION}
    _install_phone_recovery(fix,engine);_install_compact_page(fix);_install_manual_action(fix)

    @app.middleware("http")
    async def manual_edit_and_compact(request:Request,call_next):
        path=request.url.path.rstrip("/") or "/"
        if path=="/alliance/final/requirements/edit-manual" and request.method=="GET":
            sid=request.query_params.get("source_id","");row=_manual_row(engine,sid)
            if not row:return HTMLResponse(fix._page("Edit Manual Requirement","<div class=card>Manual requirement not found.</div>"),status_code=404)
            return HTMLResponse(_edit_page(fix,row),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/requirements/edit-manual" and request.method=="POST":
            form=await request.form();sid=str(form.get("source_id") or "");row=_manual_row(engine,sid)
            if not row:return HTMLResponse("Manual requirement not found",status_code=404)
            ex=_d(row.get("extracted_fields"));
            ex.update({"company_brand_person":str(form.get("company") or "").strip(),"contact_name":str(form.get("contact_name") or "").strip(),"contact_numbers":[x for x in re.findall(r"[6-9]\d{9}",str(form.get("contact") or ""))],"locations":[x.strip() for x in str(form.get("location") or "").split(",") if x.strip()],"intended_use":str(form.get("category") or "").strip(),"property_category":str(form.get("property_type") or "").strip(),"area_min_sqft":str(form.get("area_min") or "").strip(),"area_max_sqft":str(form.get("area_max") or "").strip(),"transaction_type":str(form.get("transaction") or "").strip(),"budget_max":str(form.get("budget") or "").strip(),"floor_requirement":str(form.get("floor") or "").strip()})
            with engine.begin() as c:
                c.execute(text("""UPDATE pi_requirement_gate_v1191 SET original_message=:msg,company_brand_person=:company,contact_numbers=CAST(:phones AS JSONB),locations=CAST(:loc AS JSONB),intended_use=:use,property_category=:ptype,transaction_type=:tx,floor_requirement=:floor,extracted_fields=CAST(:ex AS JSONB),updated_at=NOW() WHERE id=:id"""),{"msg":str(form.get("message") or "").strip(),"company":str(form.get("company") or "").strip(),"phones":json.dumps(ex["contact_numbers"]),"loc":json.dumps(ex["locations"]),"use":ex["intended_use"],"ptype":ex["property_category"],"tx":ex["transaction_type"],"floor":ex["floor_requirement"],"ex":json.dumps(ex),"id":row.get("id")})
            return RedirectResponse("/alliance/final/requirements/manual",303)
        return await call_next(request)
    return {"status":"REGISTERED","version":VERSION,"manual_edit":True,"compact_tables":True,"whatsapp_registry_recovery":True}
