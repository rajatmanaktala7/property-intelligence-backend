from __future__ import annotations

import html,json,re
from fastapi import Request
from fastapi.responses import HTMLResponse,RedirectResponse
from sqlalchemy import text

VERSION="1.1.0-MASTER-AND-REQUIREMENT-RESTORE"
PHONE_RE=re.compile(r"(?<!\d)(?:\+?91[\s.\-]?)?([6-9](?:[\s.\-]?\d){9})(?!\d)")

def _e(v): return html.escape("" if v is None else str(v))
def _phones(value):
    found=[]
    def walk(v):
        if isinstance(v,dict):
            for x in v.values(): walk(x)
        elif isinstance(v,(list,tuple,set)):
            for x in v: walk(x)
        else:
            s=str(v or "").replace("@s.whatsapp.net","").replace("@lid","")
            for m in PHONE_RE.finditer(s):
                p=re.sub(r"\D","",m.group(1))
                if len(p)==10 and p not in found: found.append(p)
    walk(value); return ", ".join(found)

def _clean_location(v):
    s=re.sub(r"\s+"," ",str(v or "")).strip(); low=s.lower()
    company=("royal construction","royal constructions","construction pvt","constructions pvt","builders pvt","developers pvt","construction ltd","builders ltd","developers ltd")
    return "Not captured" if (not s or any(x in low for x in company)) else s

def _page(title,body,back=True):
    b="<p><a class='btn' href='/alliance/primary'>← Command Centre</a></p>" if back else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#172033;font:14px Arial}}header{{background:#10223f;color:white;padding:17px 20px}}.wrap{{max-width:1900px;margin:auto;padding:16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}.card{{background:white;border:1px solid #d0d5dd;border-radius:12px;padding:15px;margin-bottom:12px}}a.btn,button{{display:inline-block;background:#10223f;color:white;text-decoration:none;padding:9px 11px;border:0;border-radius:7px;margin:3px}}.good{{background:#067647!important}}.tablebox{{overflow:auto;max-height:78vh;background:white}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}th,td{{border:1px solid #d0d5dd;padding:7px;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#e9eef5}}.desc{{min-width:330px;max-width:560px;white-space:pre-wrap}}input{{padding:9px;border:1px solid #98a2b3;min-width:300px}}</style></head><body><header><b>Alliance CRE Operating System</b></header><div class='wrap'>{b}<h2>{_e(title)}</h2>{body}</div></body></html>"""

def _property_hub():
    cards=[("Master Properties","/alliance/final/database/master","Complete clean property inventory from every valid source."),("Newspaper","/alliance/final/database/newspaper","Newspaper properties."),("Magazine","/alliance/final/database/magazine","Magazine properties."),("WhatsApp","/alliance/final/database/whatsapp","WhatsApp properties."),("Manual","/alliance/final/database/manual","Manual properties.")]
    body="<div class='grid'>"
    for title,href,desc in cards:
        extra="<br><br><a class='btn good' href='/property-manual'>+ Add Property</a>" if title=="Manual" else ""
        body+=f"<div class='card'><h3>{_e(title)}</h3><p>{_e(desc)}</p><a class='btn' href='{href}'>Open Database</a>{extra}</div>"
    return _page("Property Databases",body+"</div><div class='card'><b>Master Properties</b> is the total clean inventory. Source databases are views of the same system.</div>")

def _manual_page():
    return _page("Manual Data Entry","""<div class='grid'><div class='card'><h3>Add Manual Property</h3><p>Valid entry is stored under Manual and Master Properties.</p><a class='btn good' href='/property-manual'>+ Add Property</a></div><div class='card'><h3>Add Manual Requirement</h3><p>Valid entry is stored under Manual and Master Requirements.</p><a class='btn good' href='/alliance/final/requirements/add-manual'>+ Add Requirement</a></div></div>""")

def _master_property_page(engine,q=""):
    q=str(q or "").strip(); params={"n":2500}; where="1=1"
    if q: where+=" AND to_jsonb(p)::text ILIKE :q"; params["q"]="%"+q+"%"
    try:
        with engine.connect() as c: rows=c.execute(text(f"SELECT to_jsonb(p) d FROM pi_master_properties_v711 p WHERE {where} ORDER BY p.created_at DESC NULLS LAST LIMIT :n"),params).scalars().all()
    except Exception as exc: return _page("Master Properties",f"<div class='card'>Master database unavailable: {_e(type(exc).__name__)}</div>",False)
    trs=[]
    for raw in rows:
        d=raw if isinstance(raw,dict) else json.loads(raw); cr=d.get("clean_record") if isinstance(d.get("clean_record"),dict) else {}
        loc=_clean_location(d.get("locality") or d.get("city") or cr.get("location") or cr.get("locality"))
        phone=_phones([d.get("phones"),cr.get("phone_numbers"),cr.get("contact_number"),cr.get("sender_phone")]) or "Not captured"
        desc=cr.get("description") or cr.get("original_message") or cr.get("raw_text") or cr.get("configuration_details") or "Not captured"
        area=d.get("area_sqft") or cr.get("area") or "Not captured"; amount=d.get("price_raw") or cr.get("price") or cr.get("rent") or cr.get("sale_amount") or "Not captured"
        vals=[d.get("master_property_id") or d.get("canonical_id"),d.get("source_type"),loc,d.get("transaction_type"),area,amount,desc,phone,d.get("promotion_status")]
        trs.append("<tr>"+"".join(f"<td class='{'desc' if i==6 else ''}'>{_e(v if v not in (None,'') else 'Not captured')}</td>" for i,v in enumerate(vals))+"</tr>")
    search=f"<div class='card'><form><input name='q' value='{_e(q)}' placeholder='Search Master Properties'><button>Search</button></form><b>{len(rows)}</b> records shown</div>"
    heads=["Property ID","Source","Location","Rent / Sale","Area Sqft","Amount","Property Details","Contact No.","Status"]
    return _page("Master Properties",search+f"<div class='tablebox'><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs)}</tbody></table></div>",False)

def _magazine_page(engine,q=""):
    q=str(q or "").strip(); params={"n":500}; where="1=1"
    if q: where+=" AND to_jsonb(x)::text ILIKE :q"; params["q"]="%"+q+"%"
    try:
        with engine.connect() as c: rows=c.execute(text(f"SELECT to_jsonb(x) d FROM pi_magazine_workable_v12009 x WHERE {where} ORDER BY x.source_id DESC LIMIT :n"),params).scalars().all()
    except Exception as exc:return _page("Magazine Database",f"<div class='card'>Magazine database unavailable: {_e(type(exc).__name__)}</div>")
    trs=[]
    for raw in rows:
        d=raw if isinstance(raw,dict) else json.loads(raw); phone=_phones([d.get("valid_mobiles"),d.get("valid_landlines"),d.get("partial_contacts"),d.get("phone_numbers"),d.get("contact_number")]) or "Not captured"
        vals=[d.get("source_id"),_clean_location(d.get("settled_location")),d.get("settled_status"),d.get("original_raw_text"),d.get("category"),d.get("listing_type") or d.get("configuration"),f"{d.get('area') or ''} {d.get('area_unit') or ''}".strip(),d.get("floor"),d.get("price"),d.get("contact_name_company"),phone]
        trs.append("<tr>"+"".join(f"<td class='{'desc' if i==3 else ''}'>{_e(v or 'Not captured')}</td>" for i,v in enumerate(vals))+"</tr>")
    heads=["ID","Location","Quality","Description","Category","Type","Area","Floor","Amount","Contact","Phone"]
    return _page("Magazine Database",f"<form><input name='q' value='{_e(q)}' placeholder='Search Magazine database'><button>Search</button></form><br><div class='tablebox'><table><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr>{''.join(trs)}</table></div>")

def _recover_sender(engine,row):
    direct=_phones([row.get("contact"),row.get("phones"),row.get("contact_numbers")])
    if direct:return direct
    table=str(row.get("source_table") or ""); pk=str(row.get("source_pk") or "")
    candidates=[]
    try:
        with engine.connect() as c:
            if table and pk and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",table):
                reg=c.execute(text("SELECT to_regclass(:t)"),{"t":table}).scalar()
                if reg:
                    obj=c.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t WHERE CAST(COALESCE(to_jsonb(t)->>\'id\',to_jsonb(t)->>\'record_id\',to_jsonb(t)->>\'requirement_id\',to_jsonb(t)->>\'wa_requirement_id\',to_jsonb(t)->>\'message_id\') AS TEXT)=:pk LIMIT 1'),{"pk":pk}).scalar()
                    if isinstance(obj,dict): candidates.append(obj)
            for obj in list(candidates):
                mid=str(obj.get("message_id") or obj.get("wa_message_id") or "")
                if mid and c.execute(text("SELECT to_regclass('wa_messages')")).scalar():
                    msg=c.execute(text("SELECT to_jsonb(t) FROM wa_messages t WHERE CAST(message_id AS TEXT)=:m LIMIT 1"),{"m":mid}).scalar()
                    if isinstance(msg,dict): candidates.append(msg)
    except Exception: pass
    return _phones(candidates)

def _patch_requirements():
    try: import alliance_requirement_restore_v1235 as req
    except Exception as exc:return {"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}
    if getattr(req,"_alliance_rectification_v110",False):return {"status":"PATCHED"}
    original_master=req._master_rows; original_gate=req._gate_rows; original_combined=req._combined; original_hub=req._hub
    def clean_rows(engine,rows):
        for row in rows:
            p=_phones(row.get("contact")) or _recover_sender(engine,row)
            row["contact"]=p or "Not captured"
        return rows
    def master_rows(engine,source,limit=10000):return clean_rows(engine,original_master(engine,source,limit))
    def gate_rows(engine,limit=10000):return clean_rows(engine,original_gate(engine,limit))
    req._master_rows=master_rows; req._gate_rows=gate_rows
    def combined(engine,source):
        # Source pages must include Requirement Gate evidence as well as historical source tables.
        if source=="MASTER": rows,meta=original_combined(engine,source); return clean_rows(engine,rows),meta
        masters=master_rows(engine,source); source_rows,tables=req._source_rows(engine,source); gates=[r for r in gate_rows(engine,20000) if str(r.get("source") or "").upper()==source]
        all_rows=masters+gates+source_rows; seen=set(); out=[]
        for r in all_rows:
            fp=req._fingerprint(r)
            if fp in seen:continue
            seen.add(fp);out.append(r)
        out.sort(key=lambda r:str(r.get("created_at") or ""),reverse=True)
        return clean_rows(engine,out),{"master":sum(1 for r in out if r.get("is_master")),"source_only":sum(1 for r in out if not r.get("is_master")),"tables":["pi_requirement_gate_v1191"]+tables}
    req._combined=combined
    def hub(engine):
        page,details=original_hub(engine)
        page=re.sub(r"<div class=\"card\"><b>Add or open requirement sources</b>.*?</div>","",page,count=1,flags=re.S)
        page=re.sub(r"<a class=\"dbcard\" href=\"/alliance/final/requirements/social\">.*?</a>","",page,count=1,flags=re.S)
        page=page.replace("6 Requirement Databases","Master Requirement Database").replace("Master Requirements is the complete all-source evidence inventory.","Master Requirements is the total of all valid requirements from every source. Manual, Newspaper, Magazine and WhatsApp remain source-linked and are included in Master.")
        return page,details
    req._hub=hub; req._alliance_rectification_v110=True
    return {"status":"PATCHED","contacts":"SOURCE_SENDER_RECOVERY","manual_newspaper":"GATE_PLUS_SOURCE_RESTORED"}

def register(core,requirement_app=None,served_app=None):
    app=served_app or getattr(core,"app",None) or core; engine=getattr(core,"engine",None); state=_patch_requirements()
    @app.middleware("http")
    async def alliance_clean_pages(request:Request,call_next):
        path=request.url.path.rstrip("/") or "/"
        if path=="/alliance/source/newspaper":return RedirectResponse("/newspaper-v83",307)
        if path=="/alliance/source/manual":return HTMLResponse(_manual_page(),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/databases":return HTMLResponse(_property_hub(),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/database/master" and engine is not None:return HTMLResponse(_master_property_page(engine,request.query_params.get("q","")),headers={"Cache-Control":"no-store"})
        if path=="/alliance/source/magazine" and engine is not None:return HTMLResponse(_magazine_page(engine,request.query_params.get("q","")),headers={"Cache-Control":"no-store"})
        return await call_next(request)
    return {"status":"REGISTERED","version":VERSION,"master_property_nav":"REMOVED","master_location_guard":"ACTIVE","requirement_sender_recovery":"ACTIVE","manual_newspaper_requirement_restore":"ACTIVE","requirement_patch":state,"database_changed":False}
