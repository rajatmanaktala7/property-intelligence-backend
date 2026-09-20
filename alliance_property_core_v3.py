from __future__ import annotations
import html, json, re
from fastapi import Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "3.0.0-PROPERTY-CORE-ISOLATED"
SOURCES = ("MASTER","WHATSAPP","MANUAL","NEWSPAPER","MAGAZINE")
SOURCE_TABLES = {
    "MANUAL": ("pi_operational_properties",),
    "MAGAZINE": ("pi_magazine_complete_v860",),
    "NEWSPAPER": ("pi_newspaper_properties","pi_newspaper_complete","newspaper_properties"),
    "WHATSAPP": ("pi_whatsapp_property_master","pi_whatsapp_properties","wa_properties"),
}
BAD_EXACT = {"tara","royal construction","royal constructions","unknown","n/a","na"}
BAD_ROLE = re.compile(r"\b(construction|constructions|builder|builders|developer|developers|realty|properties|infra|infrastructure|owner|broker|dealer)\b",re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?([6-9]\d{9})(?!\d)")
LOC_PATTERNS = [
    r"\b(Lajpat\s+Nagar(?:\s*[- ]?\s*[1-4IVX]+)?)\b",r"\b(Amar\s+Colony)\b",r"\b(Dayanand\s+Colony)\b",
    r"\b(Vikram\s+Vihar)\b",r"\b(Maharani\s+Bagh)\b",r"\b(Mayfair\s+Garden)\b",r"\b(Malviya\s+Nagar)\b",
    r"\b(Gulmohar\s+Park)\b",r"\b(Green\s+Park)\b",r"\b(Hauz\s+Khas)\b",r"\b(Punjabi\s+Bagh)\b",
    r"\b(Paschim\s+Vihar)\b",r"\b(Rajouri\s+Garden)\b",r"\b(Janakpuri)\b",r"\b(Patel\s+Nagar)\b",
    r"\b(Saket)\b",r"\b(Noida(?:\s+Sector[- ]?\d+)?)\b",r"\b(Gurgaon(?:\s+Sector[- ]?\d+)?)\b",
    r"\b(Faridabad(?:\s+Sector[- ]?\d+)?)\b",r"\b(Vagator)\b",r"\b(Anjuna)\b",r"\b(Siolim)\b",
    r"\b(Porvorim)\b",r"\b(Calangute)\b",r"\b(Nerul)\b",r"\b(Sangolda)\b",
]

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _auth(core,req):
    fn=getattr(core,"need_login",None)
    if fn: fn(req)
def _e(v): return html.escape("" if v is None else str(v))
def _dict(v):
    if isinstance(v,dict): return dict(v)
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}
def _first(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"",[],{}): return v
    return None
def _table_exists(e,t):
    try:
        with e.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t)"),{"t":"public."+t}).scalar())
    except Exception:return False
def _flat(d):
    x=_dict(d)
    for k in ("manual_operational","magazine","newspaper","whatsapp","source_record","raw_record","whatsapp_live_clean"):
        n=x.get(k)
        if isinstance(n,dict):
            for a,b in n.items():
                if x.get(a) in (None,"",[],{}):x[a]=b
    return x
def _text(v):
    if isinstance(v,list):return ", ".join(str(x) for x in v if x not in (None,""))
    if isinstance(v,dict):return json.dumps(v,ensure_ascii=False,default=str)
    return "" if v is None else str(v).strip()
def _phones(d):
    vals=[]
    for k in ("contact_numbers","phones","contact_number","contact_phone","owner_broker_contact","owner_phone","broker_phone","phone","mobile","sender_phone"):
        v=d.get(k)
        if isinstance(v,list):vals.extend(map(str,v))
        elif v not in (None,"",{},[]):vals.append(str(v))
    out=[]
    for m in PHONE.findall(" | ".join(vals)):
        if m not in out:out.append(m)
    return ", ".join(out)
def _bad_location(v):
    s=_text(v)
    return (not s) or s.lower() in BAD_EXACT or bool(BAD_ROLE.search(s)) or bool(PHONE.search(s))
def _recover_location(d,current=""):
    for k in ("micro_market","area_name","locality","city"):
        v=_text(d.get(k))
        if v and not _bad_location(v) and v.lower()!=_text(current).lower():return v
    blob=" | ".join(_text(d.get(k)) for k in ("address","exact_address","property_address","description","original_description","source_text","raw_line","original_message","details"))
    for p in LOC_PATTERNS:
        m=re.search(p,blob,re.I)
        if m:return re.sub(r"\s+"," ",m.group(1)).strip()
    return ""
def _location(d):
    cur=_first(d,"location","locality","micro_market","area_name","city")
    if cur and not _bad_location(cur):return _text(cur)
    return _recover_location(d,cur) or "Needs verification"
def _description(d):
    v=_first(d,"description","property_description","original_description","source_text","raw_line","original_message","details","remarks","property_name")
    if v:return _text(v)
    parts=[]
    for label,keys in (("Property",("property_name","property_type","property_types")),("Area",("area_text","area","area_sqft")),
                       ("Floor",("floor","floors")),("Suitable",("suitable_for","property_category")),("Remarks",("remarks",))):
        x=_first(d,*keys)
        if x:parts.append(label+": "+_text(x))
    return " | ".join(parts) or "Not captured"
def _ptype(d):
    v=_first(d,"property_type","property_types","asset_type","subtype")
    return _text(v) or "Not captured"
def _category(d,tx):
    v=_first(d,"property_category","category")
    if v:return _text(v)
    p=_ptype(d)
    low=p.lower()
    fam="Commercial" if any(x in low for x in ("commercial","retail","office","shop","restaurant","banquet","warehouse","hotel")) else "Residential" if any(x in low for x in ("residential","villa","apartment","flat","bhk")) else ""
    side="Rent" if str(tx).upper() in ("RENT","LEASE") else "Sale" if str(tx).upper()=="SALE" else ""
    return (fam+" "+side).strip() or "Not captured"
def _area(d):
    v=_first(d,"area_text","area_display","available_area")
    if v:return _text(v)
    val=_first(d,"area_value","area_sqft","area","size")
    unit=_first(d,"area_unit")
    return (f"{_text(val)} {_text(unit)}".strip() if val else "Not captured")
def _amount(d,tx):
    # Preserve source units. Never silently reinterpret historical numeric values.
    v=_first(d,"rent_text","amount","amount_raw","price_raw")
    if v:return _text(v)
    v=_first(d,"rent_amount","monthly_rent") if str(tx).upper() in ("RENT","LEASE") else _first(d,"sale_amount","sale_price","price")
    if v not in (None,""):
        return f"{_text(v)} (unit/basis not captured)"
    return "Not captured"
def _contact_name(d):
    return _text(_first(d,"contact_name","owner_broker_name","owner_name","broker_name","sender_name","name")) or "Not captured"
def _source_id(d,source):
    return _text(_first(d,"canonical_id","property_id","property_code","source_record_id","wa_property_id","id")) or source+"-ROW"
def _row(d,source):
    tx=_text(_first(d,"transaction_type","rent_sale","rent_or_sale"))
    return {
        "id":_source_id(d,source),"location":_location(d),"description":_description(d),"category":_category(d,tx),
        "ptype":_ptype(d),"area":_area(d),"floor":_text(_first(d,"floor","floors")) or "Not captured","tx":tx or "Not captured",
        "amount":_amount(d,tx),"name":_contact_name(d),"phone":_phones(d) or "Not captured",
        "status":_text(_first(d,"availability_status","verification_status","status")) or "UNVERIFIED",
        "assigned":_text(_first(d,"assigned_to","team_member")) or "Not captured","source":source,
    }
def _master_rows(e,limit):
    sql="""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
    COALESCE(w.availability_status,'UNKNOWN') AS availability_status,COALESCE(w.assigned_to,'') AS assigned_to
    FROM pi_master_properties_v711 p LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
    WHERE UPPER(COALESCE(p.promotion_status,'')) NOT IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED','MANUAL_ARCHIVED')
    ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST LIMIT :n"""
    with e.connect() as c:raw=[dict(x) for x in c.execute(text(sql),{"n":limit}).mappings().all()]
    out=[]
    for r in raw:
        d=_flat(r.get("clean_record")); d={**d,**{k:v for k,v in r.items() if v not in (None,"")}}
        out.append(_row(d,"MASTER"))
    return out
def _source_rows(e,source,limit):
    table=next((t for t in SOURCE_TABLES.get(source,()) if _table_exists(e,t)),None)
    if not table:return []
    where=""
    if source=="MANUAL":where=" WHERE COALESCE(entry_source,'MANUAL')='MANUAL'"
    elif source=="MAGAZINE":where=" WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'"
    with e.connect() as c:
        vals=c.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t{where} LIMIT :n'),{"n":limit}).scalars().all()
    return [_row(_flat(v if isinstance(v,dict) else _dict(v)),source) for v in vals]
def _all_rows(e,source,limit):
    return _master_rows(e,limit) if source=="MASTER" else _source_rows(e,source,limit)
def _page(title,body):
    css="""body{font-family:Arial;margin:0;background:#f4f6f8;color:#172033}header{background:#10223f;color:#fff;padding:15px 22px}.w{padding:18px}.card{background:#fff;border:1px solid #d0d5dd;border-radius:9px;padding:12px;margin-bottom:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.btn{display:inline-block;padding:8px 11px;border-radius:6px;background:#10223f;color:#fff;text-decoration:none;margin:2px}.table{overflow:auto;background:#fff;border:1px solid #d0d5dd}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #e4e7ec;padding:7px;text-align:left;vertical-align:top;white-space:nowrap}.desc{white-space:normal;min-width:320px}.warn{background:#fff4e5}.good{background:#ecfdf3}input,select{padding:8px;border:1px solid #bbb;border-radius:5px}"""
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>{css}</style></head><body><header><b>Alliance Property Core V3</b><br><small>Property-only authority · Requirements are isolated and untouched</small></header><main class='w'><p><a class='btn' href='/team-dashboard-v376'>Dashboard</a> <a class='btn' href='/alliance/final/databases'>Property Databases</a> <a class='btn' href='/alliance/final/requirements'>Requirements</a></p><h2>{_e(title)}</h2>{body}</main></body></html>")
def register(core,served_app=None):
    app=served_app or _app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("Property Core V3 requires app + engine")
    @app.get("/alliance/final/databases",response_class=HTMLResponse)
    def hub(req:Request):
        _auth(core,req)
        cards="".join(f"<div class='card'><h3>{s.title()}</h3><a class='btn' href='/alliance/final/database/{s.lower()}'>Open & Search</a></div>" for s in SOURCES)
        note="<div class='card good'><b>Stable boundary:</b> Property Core owns property views only. Requirement databases remain on their separate authority. Matcher continues to read Master Properties only.</div>"
        return _page("5 Property Databases",note+"<div class='grid'>"+cards+"</div>")
    @app.get("/alliance/final/database/{source}",response_class=HTMLResponse)
    def db(req:Request,source:str,q:str=Query(""),location:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        _auth(core,req);src=source.upper()
        if src not in SOURCES:return HTMLResponse("Unknown property database",404)
        rows=_all_rows(e,src,limit)
        if q.strip():
            n=q.lower().strip();rows=[r for r in rows if n in json.dumps(r,ensure_ascii=False,default=str).lower()]
        if location.strip():
            n=location.lower().strip();rows=[r for r in rows if n in r["location"].lower()]
        form=f"<div class='card'><form><input name='q' value='{_e(q)}' placeholder='Search all property evidence'><input name='location' value='{_e(location)}' placeholder='Location'><input type='number' name='limit' value='{limit}' min='1' max='1500'><button class='btn'>Search</button></form></div>"
        tr=[]
        for r in rows:
            cl=" class='warn'" if r["location"]=="Needs verification" else ""
            vals=[r["id"],r["location"],r["description"],r["category"],r["ptype"],r["area"],r["floor"],r["tx"],r["amount"],r["name"],r["phone"],r["status"],r["assigned"],r["source"]]
            tr.append("<tr"+cl+">"+"".join(f"<td class='{'desc' if i==2 else ''}'>{_e(v)}</td>" for i,v in enumerate(vals))+"</tr>")
        heads=["Property ID","Location","Description / Address","Property Category","Property Type","Area","Floor","Rent/Sale","Amount","Contact Name","Contact No.","Status","Assigned To","Source"]
        body=form+"<div class='card'><b>Source truth:</b> this page reads the "+("canonical Master." if src=="MASTER" else "underlying "+src.title()+" source table directly.")+" Invalid locations are not guessed; explicit description/address evidence is used, otherwise the record is marked Needs verification.</div>"
        body+=f"<div class='table'><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(tr) if tr else '<tr><td colspan=14>No records found in source authority.</td></tr>'}</tbody></table></div>"
        return _page(src.title()+" Property Database",body)
    return {"status":"REGISTERED","version":VERSION,"scope":"PROPERTY_ONLY","requirements_touched":False}
