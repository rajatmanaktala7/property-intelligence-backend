from __future__ import annotations

import html, json, re
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text, inspect

VERSION = "1.2.0-REQUIREMENT-RESTORE-COMPACT-UI"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s.\-]?)?([6-9](?:[\s.\-]?\d){9})(?!\d)")
SOURCES = ("MASTER","NEWSPAPER","MANUAL","MAGAZINE","WHATSAPP")

def _e(v): return html.escape("" if v is None else str(v))
def _phones(*values):
    found=[]
    def walk(v):
        if isinstance(v,dict):
            for x in v.values(): walk(x)
        elif isinstance(v,(list,tuple,set)):
            for x in v: walk(x)
        else:
            s=str(v or "").replace("@s.whatsapp.net","").replace("@c.us","")
            for m in PHONE_RE.finditer(s):
                p=re.sub(r"\D","",m.group(1))
                if len(p)==10 and p not in found: found.append(p)
    for v in values: walk(v)
    return ", ".join(found)

def _dict(v):
    if isinstance(v,dict): return dict(v)
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception: return {}
    return {}

def _first(d,keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"",[],{}): return v
    return ""

def _clean_location(v):
    s=re.sub(r"\s+"," ",str(v or "")).strip(); low=s.lower()
    bad=("royal construction","royal constructions","construction pvt","constructions pvt","builders pvt","developers pvt","construction ltd","builders ltd","developers ltd")
    return "Not captured" if not s or any(x in low for x in bad) else s

def _nav():
    return "<div class='nav'><a class='btn' href='#' onclick='history.back();return false'>← Previous Page</a><a class='btn' href='/alliance/primary'>Dashboard</a></div>"

def _page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#172033;font:13px Arial}}header{{background:#10223f;color:white;padding:12px 16px}}.wrap{{max-width:1920px;margin:auto;padding:10px}}.nav{{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}}a.btn,button{{display:inline-block;background:#10223f;color:white;text-decoration:none;padding:7px 9px;border:0;border-radius:5px;font-weight:700}}.grid{{display:grid;grid-template-columns:repeat(5,minmax(145px,1fr));gap:7px}}.card{{background:white;border:1.5px solid #98a2b3;border-radius:8px;padding:9px;margin-bottom:8px}}.tablebox{{overflow:auto;max-height:78vh;background:white;border:2px solid #667085;border-radius:6px}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px;font-weight:600}}th,td{{border:1.4px solid #98a2b3;padding:5px 6px;text-align:left;vertical-align:top;line-height:1.25}}th{{position:sticky;top:0;background:#dce6f2;color:#10223f;font-weight:800;z-index:3}}tr:nth-child(even){{background:#f8fafc}}.desc{{min-width:230px;max-width:390px;white-space:pre-wrap}}.loc{{min-width:85px;max-width:150px;white-space:normal}}input,select{{padding:7px;border:1.4px solid #667085;border-radius:5px;min-width:180px}}h2{{margin:7px 0 9px}}h3{{margin:3px 0 6px}}.muted{{color:#667085;font-weight:400}}@media(max-width:900px){{.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><header><b>Alliance CRE Operating System</b></header><div class='wrap'>{_nav()}<h2>{_e(title)}</h2>{body}</div></body></html>"""

def _property_hub():
    cards=[("Master Properties","/alliance/final/database/master"),("Newspaper","/alliance/final/database/newspaper"),("Magazine","/alliance/final/database/magazine"),("WhatsApp","/alliance/final/database/whatsapp"),("Manual","/alliance/final/database/manual")]
    body="<div class='grid'>"+"".join(f"<div class='card'><h3>{t}</h3><a class='btn' href='{u}'>Open</a>{('<br><br><a class=btn href=/property-manual>+ Add Property</a>' if t=='Manual' else '')}</div>" for t,u in cards)+"</div>"
    return _page("Property Databases",body)

def _master_properties(engine,q=""):
    q=str(q or "").strip(); params={"n":2500}; where="1=1"
    if q: where+=" AND to_jsonb(p)::text ILIKE :q"; params["q"]="%"+q+"%"
    try:
        with engine.connect() as c: rows=c.execute(text(f"SELECT to_jsonb(p) FROM pi_master_properties_v711 p WHERE {where} ORDER BY p.created_at DESC NULLS LAST LIMIT :n"),params).scalars().all()
    except Exception as exc: return _page("Master Properties",f"<div class=card>Database unavailable: {_e(type(exc).__name__)}</div>")
    trs=[]
    for raw in rows:
        d=_dict(raw); cr=_dict(d.get("clean_record")); loc=_clean_location(d.get("locality") or d.get("city") or _first(cr,["location","locality","city"]))
        phone=_phones(d.get("phones"),cr) or "Not captured"
        desc=_first(cr,["description","original_message","raw_text","configuration_details"]) or "Not captured"
        vals=[d.get("master_property_id") or d.get("canonical_id"),d.get("source_type"),loc,d.get("transaction_type"),d.get("area_sqft") or _first(cr,["area","area_sqft"]),d.get("price_raw") or _first(cr,["price","rent","sale_amount"]),desc,phone,d.get("promotion_status")]
        trs.append("<tr>"+"".join(f"<td class='{('loc' if i==2 else 'desc' if i==6 else '')}'>{_e(v if v not in (None,'') else 'Not captured')}</td>" for i,v in enumerate(vals))+"</tr>")
    heads=["Property ID","Source","Location","Rent / Sale","Area Sqft","Amount","Property Details","Contact No.","Status"]
    top=f"<div class=card><form><input name=q value='{_e(q)}' placeholder='Search Master Properties'> <button>Search</button></form><b>{len(rows)}</b> records shown</div>"
    return _page("Master Properties",top+f"<div class=tablebox><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs)}</tbody></table></div>")

def _classify_source(v):
    s=str(v or "").upper()
    if "NEWSPAPER" in s: return "NEWSPAPER"
    if "MANUAL" in s: return "MANUAL"
    if "MAGAZINE" in s: return "MAGAZINE"
    if "WHATSAPP" in s or s.startswith("WA_") or "WAI_" in s: return "WHATSAPP"
    return "OTHER"

def _sender_from_whatsapp(row):
    direct=_phones(row)
    if direct: return direct
    try:
        import whatsapp_live_bridge as live
        we=live.wa_engine
        if we is None: return ""
        mid=str(_first(row,["message_id","wa_message_id","external_message_id"]) or "")
        rid=str(_first(row,["wa_requirement_id","source_pk","requirement_id","id"]) or "")
        with we.connect() as c:
            if rid:
                r=c.execute(text("SELECT to_jsonb(x) FROM wa_requirements x WHERE CAST(wa_requirement_id AS TEXT)=:r OR CAST(id AS TEXT)=:r LIMIT 1"),{"r":rid}).scalar()
                if isinstance(r,dict):
                    p=_phones(r)
                    if p:return p
                    mid=mid or str(r.get("message_id") or "")
            if mid:
                m=c.execute(text("SELECT to_jsonb(x) FROM wa_messages x WHERE CAST(message_id AS TEXT)=:m LIMIT 1"),{"m":mid}).scalar()
                if isinstance(m,dict):
                    p=_phones(m.get("sender_phone"),m)
                    if p:return p
    except Exception: pass
    return ""

def _norm_req(obj,source_table=""):
    d=dict(obj or {}); ex=_dict(d.get("extracted_fields")); cr=_dict(d.get("clean_record")); merged={}; merged.update(ex); merged.update(cr); merged.update({k:v for k,v in d.items() if v not in (None,"",[],{})})
    src=_classify_source(d.get("source_type") or d.get("source") or source_table)
    msg=_first(merged,["original_message","requirement_message","raw_text","message","requirement_text","requirement","description","additional_points","remarks","notes"]) or ""
    contact=_phones(merged)
    if src=="WHATSAPP" and not contact: contact=_sender_from_whatsapp(merged)
    loc=_first(merged,["locations","preferred_locations","preferred_location","location","locality","city","area_name","micro_market"])
    if isinstance(loc,list):loc=", ".join(str(x) for x in loc if x)
    area=_first(merged,["area_sqft","requirement_sqft","required_area","area","area_min_sqft","minimum_area_sqft"])
    budget=_first(merged,["budget_max","budget","sale_budget","rent_budget","budget_raw"])
    return {"date":_first(merged,["created_at","message_timestamp","timestamp","date","captured_at"]),"message":msg,"company":_first(merged,["company_name","brand_name","client_company","company","retailer_name","company_brand_person"]),"name":_first(merged,["contact_name","client_name","sender_name","name"]),"contact":contact or "Not captured","location":loc or "Not captured","category":_first(merged,["intended_use","suitable_category","category","property_category"]),"ptype":_first(merged,["property_type","required_property_type","asset_type"]),"area":area,"transaction":_first(merged,["transaction_type","transaction","rent_sale","rent_or_sale","deal_type"]),"budget":budget,"source":src if src!='OTHER' else source_table,"source_id":_first(merged,["source_pk","wa_requirement_id","requirement_id","record_id","id"])}

def _requirement_tables(engine):
    try:
        names=inspect(engine).get_table_names()
    except Exception:return []
    skip=("match","workflow","action","audit","review","repair","task","journal","score","metric","test","mapping","source_link","archive","history","log","semantic","training")
    return [n for n in names if "requirement" in n.lower() and not any(x in n.lower() for x in skip)]

def _requirement_rows(engine,source,limit=1800):
    rows=[]; seen=set()
    def add(obj,table):
        r=_norm_req(obj,table); src=r["source"]
        if source!="MASTER" and src!=source:return
        seed="|".join(str(r.get(k) or "").lower() for k in ("message","contact","location","company","source_id"))
        if seed in seen or not any(r.get(k) not in (None,"","Not captured") for k in ("message","contact","location","company")):return
        seen.add(seed);rows.append(r)
    # Requirement Gate is the all-source restoration inventory.
    try:
        with engine.connect() as c:
            data=c.execute(text("SELECT to_jsonb(g) FROM pi_requirement_gate_v1191 g WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED') ORDER BY created_at DESC NULLS LAST,id DESC LIMIT :n"),{"n":5000}).scalars().all()
        for x in data:add(_dict(x),"pi_requirement_gate_v1191")
    except Exception:pass
    # Canonical Master rows.
    try:
        with engine.connect() as c:data=c.execute(text("SELECT to_jsonb(r) FROM pi_master_requirements_v711 r ORDER BY created_at DESC NULLS LAST LIMIT :n"),{"n":5000}).scalars().all()
        for x in data:add(_dict(x),"pi_master_requirements_v711")
    except Exception:pass
    # Historical source requirement tables, including Manual and Newspaper.
    for table in _requirement_tables(engine):
        if table in ("pi_requirement_gate_v1191","pi_master_requirements_v711"):continue
        hint=_classify_source(table)
        if source!="MASTER" and hint not in (source,"OTHER"):continue
        try:
            with engine.connect() as c:data=c.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY 1 LIMIT :n'),{"n":3000}).scalars().all()
            for x in data:add(_dict(x),table)
        except Exception:continue
    rows=rows[:limit]
    return rows

def _requirements_page(engine,source,q=""):
    rows=_requirement_rows(engine,source,1800); q=str(q or "").strip().lower()
    if q:rows=[r for r in rows if q in " ".join(str(v or "") for v in r.values()).lower()]
    trs=[]
    for r in rows:
        vals=[r["date"],r["message"],r["company"],r["name"],r["contact"],r["location"],r["category"],r["ptype"],r["area"],r["transaction"],r["budget"],r["source"],r["source_id"]]
        trs.append("<tr>"+"".join(f"<td class='{('desc' if i==1 else 'loc' if i==5 else '')}'>{_e(v if v not in (None,'') else 'Not captured')}</td>" for i,v in enumerate(vals))+"</tr>")
    heads=["Date / Time","Original Requirement","Client / Company","Contact Name","Contact No.","Location","Category / Use","Property Type","Area","Rent / Sale","Budget","Source","Source ID"]
    top=f"<div class=card><form><input name=q value='{_e(q)}' placeholder='Search requirements'> <button>Search</button></form><b>{len(rows)}</b> restored/visible requirements. Master is the all-source total.</div>"
    return _page(("Master Requirements" if source=='MASTER' else source.title()+" Requirements"),top+f"<div class=tablebox><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan=13>No restored requirements found in this source.</td></tr>'}</tbody></table></div>")

def _requirement_hub():
    cards=[("Master Requirements","master"),("WhatsApp","whatsapp"),("Newspaper","newspaper"),("Magazine","magazine"),("Manual","manual")]
    body="<div class=grid>"+"".join(f"<div class=card><h3>{t}</h3><a class=btn href='/alliance/final/requirements/{s}'>Open</a>{('<br><br><a class=btn href=/alliance/final/requirements/add-manual>+ Add Requirement</a>' if s=='manual' else '')}</div>" for t,s in cards)+"</div>"
    return _page("Requirement Databases",body)

def _manual_page():
    return _page("Manual Data Entry","<div class=grid><div class=card><h3>Add Manual Property</h3><a class=btn href=/property-manual>+ Add Property</a></div><div class=card><h3>Add Manual Requirement</h3><a class=btn href=/alliance/final/requirements/add-manual>+ Add Requirement</a></div></div>")

def _whatsapp_requirements():
    try:
        import whatsapp_live_bridge as live
        we=live.wa_engine
        if we is None:return _page("WhatsApp Live Requirements","<div class=card>WhatsApp database unavailable.</div>")
        with we.connect() as c:
            data=c.execute(text("""SELECT to_jsonb(x) FROM (SELECT r.*,COALESCE(r.contact_phone,m.sender_phone) recovered_sender_phone,COALESCE(m.message_timestamp,r.created_at::text) req_time FROM wa_requirements r LEFT JOIN wa_messages m ON m.message_id=r.message_id WHERE r.status='ACTIVE' ORDER BY r.id DESC LIMIT 1500) x""")).scalars().all()
        trs=[]
        for raw in data:
            d=_dict(raw); phone=_phones(d.get("contact_phone"),d.get("recovered_sender_phone"),d) or "Not captured"
            vals=[d.get("req_time"),d.get("raw_text"),d.get("company_name"),d.get("contact_name"),phone,d.get("preferred_locations"),d.get("property_type"),d.get("minimum_area_sqft"),d.get("maximum_area_sqft"),d.get("transaction_type"),d.get("budget_max_inr"),d.get("wa_requirement_id")]
            trs.append("<tr>"+"".join(f"<td class='{('desc' if i==1 else 'loc' if i==5 else '')}'>{_e(v if v not in (None,'') else 'Not captured')}</td>" for i,v in enumerate(vals))+"</tr>")
        heads=["Date / Time","Original Requirement","Company","Contact Name","Contact No.","Location","Property Type","Area Min","Area Max","Rent / Sale","Budget","ID"]
        return _page("WhatsApp Live Requirements",f"<div class=card><b>{len(data)}</b> active requirements. Sender number is recovered directly from the linked WhatsApp message when contact_phone is blank.</div><div class=tablebox><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs)}</tbody></table></div>")
    except Exception as exc:return _page("WhatsApp Live Requirements",f"<div class=card>Unavailable: {_e(type(exc).__name__)}</div>")

def register(core,requirement_app=None,served_app=None):
    app=served_app or getattr(core,"app",None) or core; engine=getattr(core,"engine",None)
    @app.middleware("http")
    async def clean_database_pages(request:Request,call_next):
        path=request.url.path.rstrip("/") or "/"
        if path=="/alliance/source/newspaper":return RedirectResponse("/newspaper-v83",307)
        if path=="/alliance/source/manual":return HTMLResponse(_manual_page(),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/databases":return HTMLResponse(_property_hub(),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/database/master" and engine is not None:return HTMLResponse(_master_properties(engine,request.query_params.get("q","")),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/requirements":return HTMLResponse(_requirement_hub(),headers={"Cache-Control":"no-store"})
        if path.startswith("/alliance/final/requirements/") and engine is not None:
            src=path.rsplit("/",1)[-1].upper()
            if src in SOURCES:return HTMLResponse(_requirements_page(engine,src,request.query_params.get("q","")),headers={"Cache-Control":"no-store"})
        if path=="/whatsapp-live/requirements":return HTMLResponse(_whatsapp_requirements(),headers={"Cache-Control":"no-store"})
        return await call_next(request)
    return {"status":"REGISTERED","version":VERSION,"master_nav":"PREVIOUS_PLUS_DASHBOARD","compact_master_location":True,"bold_tables":True,"master_requirements":"DIRECT_STABLE_PAGE","historical_requirements":"GATE_CANONICAL_PLUS_SOURCE_TABLES","whatsapp_sender_contact":"LINKED_MESSAGE_RECOVERY","database_changed":False}
