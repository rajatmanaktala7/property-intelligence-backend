from __future__ import annotations

import html
import json
import re

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "1.0.1-CLEAN-SOURCE-AND-CONTACT-RECTIFICATION"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s.\-]?)?([6-9](?:[\s.\-]?\d){9})(?!\d)")


def _e(v): return html.escape("" if v is None else str(v))

def _phones(value):
    found=[]
    def walk(v):
        if isinstance(v,dict):
            for x in v.values(): walk(x)
        elif isinstance(v,(list,tuple,set)):
            for x in v: walk(x)
        else:
            s=str(v or "").replace("@s.whatsapp.net","")
            for m in PHONE_RE.finditer(s):
                p=re.sub(r"\D","",m.group(1))
                if len(p)==10 and p not in found: found.append(p)
    walk(value); return ", ".join(found)

def _clean_location(v):
    s=re.sub(r"\s+"," ",str(v or "")).strip(); low=s.lower()
    if any(x in low for x in ("royal construction","construction pvt","constructions pvt","builders pvt","developers pvt")): return "Not captured"
    return s or "Not captured"

def _page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#172033;font:14px Arial}}header{{background:#10223f;color:white;padding:17px 20px}}.wrap{{max-width:1800px;margin:auto;padding:16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}}.card{{background:white;border:1px solid #d0d5dd;border-radius:12px;padding:15px}}a.btn,button{{display:inline-block;background:#10223f;color:white;text-decoration:none;padding:9px 11px;border:0;border-radius:7px;margin:3px}}.good{{background:#067647!important}}.tablebox{{overflow:auto;max-height:76vh;background:white}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}th,td{{border:1px solid #d0d5dd;padding:7px;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#e9eef5}}.desc{{min-width:340px;max-width:560px;white-space:pre-wrap}}input{{padding:9px;border:1px solid #98a2b3;min-width:300px}}</style></head><body><header><b>Alliance CRE Operating System</b></header><div class='wrap'><p><a class='btn' href='/alliance/primary'>← Command Centre</a></p><h2>{_e(title)}</h2>{body}</div></body></html>"""

def _property_hub():
    cards=[("Master Properties","/alliance/final/database/master","Complete canonical property inventory from every valid source."),("Newspaper","/alliance/final/database/newspaper","Clean Newspaper properties saved into Master."),("Magazine","/alliance/final/database/magazine","Clean Magazine properties saved into Master."),("WhatsApp","/alliance/final/database/whatsapp","Clean WhatsApp properties saved into Master."),("Manual","/alliance/final/database/manual","Manual properties saved into Master.")]
    body="<div class='grid'>"
    for title,href,desc in cards:
        extra="<br><br><a class='btn good' href='/property-manual'>+ Add Property</a>" if title=="Manual" else ""
        body+=f"<div class='card'><h3>{_e(title)}</h3><p>{_e(desc)}</p><a class='btn' href='{href}'>Open Database</a>{extra}</div>"
    body+="</div><div class='card' style='margin-top:12px'><b>Master rule:</b> Master Properties is the total clean property inventory. Newspaper, Magazine, WhatsApp and Manual are source views of the same canonical system.</div>"
    return _page("Property Databases",body)

def _manual_page():
    return _page("Manual Data Entry","""<div class='grid'><div class='card'><h3>Add Manual Property</h3><p>Add property data. A valid new entry flows to Manual and Master Properties.</p><a class='btn good' href='/property-manual'>+ Add Property</a></div><div class='card'><h3>Add Manual Requirement</h3><p>Add requirement data. A valid new entry flows to Manual and Master Requirements.</p><a class='btn good' href='/alliance/final/requirements/add-manual'>+ Add Requirement</a></div></div>""")

def _magazine_page(engine,q=""):
    q=str(q or "").strip(); params={"n":500}; where="1=1"
    if q: where+=" AND to_jsonb(x)::text ILIKE :q"; params["q"]="%"+q+"%"
    try:
        with engine.connect() as c: rows=c.execute(text(f"SELECT to_jsonb(x) d FROM pi_magazine_workable_v12009 x WHERE {where} ORDER BY x.source_id DESC LIMIT :n"),params).scalars().all()
    except Exception as exc: return _page("Magazine Database",f"<div class='card'>Magazine database unavailable: {_e(type(exc).__name__)}</div>")
    trs=[]
    for raw in rows:
        d=raw if isinstance(raw,dict) else json.loads(raw); loc=_clean_location(d.get("settled_location")); phone=_phones([d.get("valid_mobiles"),d.get("valid_landlines"),d.get("partial_contacts"),d.get("phone_numbers"),d.get("contact_number")]) or "Not captured"
        vals=[d.get("source_id"),loc,d.get("settled_status"),d.get("original_raw_text"),d.get("category"),d.get("listing_type") or d.get("configuration"),f"{d.get('area') or ''} {d.get('area_unit') or ''}".strip(),d.get("floor"),d.get("price"),d.get("contact_name_company"),phone]
        trs.append("<tr>"+"".join(f"<td class='{'desc' if i==3 else ''}'>{_e(v or 'Not captured')}</td>" for i,v in enumerate(vals))+"</tr>")
    search=f"<form><input name='q' value='{_e(q)}' placeholder='Search Magazine database'><button>Search</button></form><br>"; heads=["ID","Location","Quality","Description","Category","Type","Area","Floor","Amount","Contact","Phone"]
    return _page("Magazine Database",search+f"<div class='tablebox'><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan=11>No records found</td></tr>'}</tbody></table></div>")

def _patch_requirements():
    try: import alliance_requirement_restore_v1235 as req
    except Exception as exc: return {"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}
    if not getattr(req,"_alliance_contact_rectification_v1",False):
        original_master=req._master_rows; original_gate=req._gate_rows; original_hub=req._hub
        def master_rows(engine,source,limit=10000):
            rows=original_master(engine,source,limit)
            for row in rows: row["contact"]=_phones(row.get("contact")) or ""
            return rows
        def gate_rows(engine,limit=10000):
            rows=original_gate(engine,limit)
            for row in rows: row["contact"]=_phones(row.get("contact")) or ""
            return rows
        def hub(engine):
            page,details=original_hub(engine)
            page=re.sub(r"<div class=\"card\"><b>Add or open requirement sources</b>.*?</div>","",page,count=1,flags=re.S)
            page=re.sub(r"<a class=\"dbcard\" href=\"/alliance/final/requirements/social\">.*?</a>","",page,count=1,flags=re.S)
            page=page.replace("6 Requirement Databases","Master Requirement Database").replace("Master Requirements is the complete all-source evidence inventory.","Master Requirements is the total of all valid requirements from every source. New valid entries remain source-linked and are also represented in Master.")
            return page,details
        req._master_rows=master_rows; req._gate_rows=gate_rows; req._hub=hub; req._alliance_contact_rectification_v1=True
    return {"status":"PATCHED","module":"alliance_requirement_restore_v1235"}

def register(core,requirement_app=None,served_app=None):
    app=served_app or getattr(core,"app",None) or core; engine=getattr(core,"engine",None); req_state=_patch_requirements()
    @app.middleware("http")
    async def clean_source_pages(request:Request,call_next):
        path=request.url.path.rstrip("/") or "/"
        if path=="/alliance/source/newspaper":
            try: core.need_login(request)
            except Exception: return await call_next(request)
            return RedirectResponse("/newspaper-v83",status_code=307)
        if path=="/alliance/source/manual":
            try: core.need_login(request)
            except Exception: return await call_next(request)
            return HTMLResponse(_manual_page(),headers={"Cache-Control":"no-store"})
        if path=="/alliance/final/databases":
            try: core.need_login(request)
            except Exception: return await call_next(request)
            return HTMLResponse(_property_hub(),headers={"Cache-Control":"no-store"})
        if path=="/alliance/source/magazine" and engine is not None:
            try: core.need_login(request)
            except Exception: return await call_next(request)
            return HTMLResponse(_magazine_page(engine,request.query_params.get("q","")),headers={"Cache-Control":"no-store"})
        return await call_next(request)
    return {"status":"REGISTERED","version":VERSION,"newspaper_page":"NEWSPAPER_V83_CAPTURE_ONLY","manual_page":"ADD_ONLY","magazine_navigation":"CLEAN","magazine_phone_format":"PLAIN_NUMBERS","company_as_location_guard":True,"master_property_hub":"SOURCE_SECTIONS_ONLY","requirement_contacts":"NORMALIZED","requirement_master":"ALL_SOURCE_TOTAL","requirement_patch":req_state,"database_changed":False}
