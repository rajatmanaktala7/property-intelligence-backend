from __future__ import annotations
import html, json, re
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="11.0.0-KNOWN-GOOD-PROPERTY-AUTHORITY"
SOURCES=("MASTER","NEWSPAPER","WHATSAPP","MAGAZINE","MANUAL")
CATEGORY_OPTIONS=("Residential Sale","Residential Rent","Commercial Sale","Commercial Rent","Industrial Sale","Industrial Rent","Farmhouse Sale","Farmhouse Rent")

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _e(v): return html.escape("" if v is None else str(v))
def _shown(v): return "Not captured" if v in (None,"",[],{}) else str(v)
def _dict(v):
    if isinstance(v,dict): return v
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}
def _flat_record(d):
    base=_dict(d)
    for nest in ("manual_operational","magazine","newspaper","whatsapp","source_record","raw_record","whatsapp_live_clean"):
        x=base.get(nest)
        if isinstance(x,dict):
            for k,v in x.items():
                if base.get(k) in (None,"",[],{}): base[k]=v
    return base

def _display_text(v):
    if isinstance(v,list): return ", ".join(str(x) for x in v if x not in (None,""))
    if isinstance(v,dict): return json.dumps(v,ensure_ascii=False,default=str)
    return "" if v is None else str(v).strip()

def _first(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"",[],{}): return v
    return None
def _fmt_dt(v):
    if not v:return ""
    try:return v.strftime("%d-%m-%Y %I:%M %p")
    except Exception:return str(v)
def _src_pat(source):
    return {"NEWSPAPER":"%NEWSPAPER%","WHATSAPP":"%WHATSAPP%","MAGAZINE":"%MAGAZINE%","MANUAL":"%MANUAL%"}.get(source)
def _source_name(e,cid,etype):
    try:
        with e.connect() as c:
            r=c.execute(text("""SELECT source_type,source_table FROM pi_master_source_links_v711
            WHERE canonical_id=:id AND master_entity_type=:et ORDER BY created_at DESC,id DESC LIMIT 1"""),{"id":cid,"et":etype}).mappings().first()
        if not r:return ""
        a=(r.get("source_type") or "").strip(); b=(r.get("source_table") or "").strip()
        return a if not b or b==a else f"{a} · {b}"
    except Exception:return ""
def _contacts(cr):
    name=_first(cr,"contact_name","owner_name","broker_name","client_name","sender_name","name") or ""
    vals=[]
    for k in ("contact_number","contact_phone","owner_contact","owner_phone","broker_contact","broker_phone","phone","mobile"):
        v=cr.get(k)
        if v not in (None,"",[],{}): vals.append(str(v))
    p=cr.get("phones")
    if isinstance(p,list): vals.extend(str(x) for x in p if x)
    blob=" | ".join(vals)
    phones=[]
    for x in re.findall(r"(?<!\d)(?:\+?91[-\s]?)?([6-9]\d{9})(?!\d)",blob):
        if x not in phones: phones.append(x)
    return name,", ".join(phones)
def _clean_location_value(value, cr):
    loc=str(value or "").strip()
    bad_words=r"\b(construction|constructions|builder|builders|developer|developers|realty|properties|property|infra|infrastructure|owner|broker|dealer)\b"
    def bad(v):
        s=str(v or "").strip()
        return (not s) or s.lower() in {"tara","royal construction","royal constructions"} or bool(re.search(bad_words,s.lower()))
    if loc and not bad(loc):
        return loc
    for k in ("micro_market","area_name","locality","city","address","exact_address","property_address"):
        v=cr.get(k)
        if v and not bad(v) and str(v).strip().lower()!=loc.lower():
            return str(v).strip()
    blob=" | ".join(str(cr.get(k) or "") for k in ("description","property_description","original_description","original_message","raw_line","source_text","details","address","exact_address","property_address"))
    patterns=[
        r"\b(Lajpat\s+Nagar(?:\s*[- ]?\s*[1-4IVX]+)?)\b",r"\b(Amar\s+Colony)\b",r"\b(Dayanand\s+Colony)\b",
        r"\b(Vikram\s+Vihar)\b",r"\b(Maharani\s+Bagh)\b",r"\b(Mayfair\s+Garden)\b",r"\b(Malviya\s+Nagar)\b",
        r"\b(Gulmohar\s+Park)\b",r"\b(Green\s+Park(?:\s+Extn)?)\b",r"\b(Hauz\s+Khas)\b",r"\b(Punjabi\s+Bagh)\b",
        r"\b(Paschim\s+Vihar)\b",r"\b(Rajouri\s+Garden)\b",r"\b(Janakpuri)\b",r"\b(Patel\s+Nagar)\b",
        r"\b(Saket)\b",r"\b(Vasant\s+Vihar)\b",r"\b(Vasant\s+Kunj)\b",r"\b(Safdarjung(?:\s+Development\s+Area|\s+Enclave)?)\b",
        r"\b(East\s+of\s+Kailash)\b",r"\b(Greater\s+Kailash(?:\s+[12])?)\b",r"\b(Defence\s+Colony)\b",
        r"\b(Chitranjan\s+Park)\b",r"\b(Jor\s+Bagh)\b",r"\b(Chanakyapuri)\b",r"\b(Uday\s+Park)\b",
        r"\b(Noida(?:\s+Sector[- ]?\d+)?)\b",r"\b(Gurgaon(?:\s+Sector[- ]?\d+)?)\b",r"\b(Faridabad(?:\s+Sector[- ]?\d+)?)\b",
        r"\b(Vagator)\b",r"\b(Anjuna)\b",r"\b(Siolim)\b",r"\b(Porvorim)\b",r"\b(Calangute)\b",r"\b(Nerul)\b",
    ]
    for p in patterns:
        m=re.search(p,blob,re.I)
        if m:return re.sub(r"\s+"," ",m.group(1)).strip()
    return "Needs verification"

def _property_category(cr,tx):
    explicit=_first(cr,"property_category","category")
    if explicit and str(explicit).strip() in CATEGORY_OPTIONS:return str(explicit).strip()
    asset=_first(cr,"asset_class","property_class","use_type")
    if asset and tx:
        a=str(asset).strip().title(); t="Rent" if str(tx).upper() in ("RENT","LEASE") else "Sale" if str(tx).upper()=="SALE" else ""
        cand=f"{a} {t}".strip()
        if cand in CATEGORY_OPTIONS:return cand
    return explicit or ""
def _property_rows(e,source,q,location,category,transaction,status,assigned,limit):
    def source_rows(table,where,order):
        with e.connect() as cx:
            exists=cx.execute(text("SELECT to_regclass(:t)"),{"t":"public."+table}).scalar()
            if not exists:return []
            raw=cx.execute(text(f"""SELECT to_jsonb(t) FROM {table} t {where}
                AND (:q='%%' OR to_jsonb(t)::text ILIKE :q)
                {order} LIMIT :n"""),{"q":f"%{q.strip()}%","n":limit}).scalars().all()
        out=[]
        for x in raw:
            d=x if isinstance(x,dict) else json.loads(x)
            sid=str(_first(d,"property_code","property_id","source_record_id","id") or "")
            out.append({
                "canonical_id":source+"-SOURCE-"+sid,
                "locality":_first(d,"location","locality","city") or "",
                "city":_first(d,"city") or "",
                "transaction_type":_first(d,"transaction_type","rent_sale","rent_or_sale") or "",
                "area_sqft":_first(d,"area_sqft","area_value","area","available_area") or "",
                "price_raw":_first(d,"rent_text","amount","amount_raw","rent_amount","sale_amount","price_raw","price") or "",
                "created_at":_first(d,"created_at","entry_datetime","entry_date"),
                "updated_at":_first(d,"updated_at","created_at","entry_datetime","entry_date"),
                "verification_status":_first(d,"verification_status") or "UNVERIFIED",
                "availability_status":_first(d,"availability_status","status") or "UNKNOWN",
                "assigned_to":_first(d,"assigned_to","team_member") or "",
                "clean_record":dict(d,source_table=table,source_pk=sid,source_type=source),
            })
        return out

    if source=="MANUAL":
        rows=source_rows("pi_operational_properties","WHERE COALESCE(entry_source,'MANUAL')='MANUAL'","ORDER BY COALESCE(updated_at,created_at) DESC NULLS LAST")
    elif source=="MAGAZINE":
        rows=source_rows("pi_magazine_complete_v860","WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'","ORDER BY COALESCE(updated_at,created_at) DESC NULLS LAST,id DESC")
    else:
        pat=_src_pat(source);sc=""
        if pat:
            sc="""AND EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.canonical_id=p.canonical_id
            AND l.master_entity_type='PROPERTY' AND (UPPER(COALESCE(l.source_type,'')) LIKE :pat OR UPPER(COALESCE(l.source_table,'')) LIKE :pat))"""
        sql=f"""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
        COALESCE(w.availability_status,'UNKNOWN') availability_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to
        FROM pi_master_properties_v711 p
        LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
        LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=p.canonical_id
        WHERE NOT EXISTS(SELECT 1 FROM pi_property_archive_v801 ar WHERE ar.canonical_id=p.canonical_id AND ar.restored_at IS NULL)
        AND UPPER(COALESCE(p.promotion_status,'')) NOT IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED','MANUAL_ARCHIVED')
        {sc}
        AND (:q='%%' OR p.canonical_id ILIKE :q OR COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q)
        AND (:loc='%%' OR COALESCE(p.locality,'') ILIKE :loc OR COALESCE(p.city,'') ILIKE :loc OR COALESCE(p.clean_record::text,'') ILIKE :loc)
        AND (:tx='' OR UPPER(COALESCE(p.transaction_type,''))=:tx)
        AND (:st='' OR UPPER(COALESCE(w.availability_status,w.verification_status,'UNVERIFIED'))=:st)
        AND (:asgn='%%' OR COALESCE(w.assigned_to,a.assigned_to,'') ILIKE :asgn)
        ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST LIMIT :n"""
        with e.connect() as cx:
            rows=[dict(x) for x in cx.execute(text(sql),{"q":f"%{q.strip()}%","loc":f"%{location.strip()}%","tx":transaction.upper().strip(),
            "st":status.upper().strip(),"asgn":f"%{assigned.strip()}%","n":limit,"pat":pat or ""}).mappings().all()]
    def blob(r):
        cr=_flat_record(r.get("clean_record"))
        return " ".join(str(x or "") for x in (r.get("canonical_id"),r.get("locality"),r.get("city"),r.get("transaction_type"),r.get("price_raw"),json.dumps(cr,ensure_ascii=False,default=str))).lower()
    if q.strip():
        n=q.strip().lower();rows=[r for r in rows if n in blob(r)]
    if location.strip():
        n=location.strip().lower();rows=[r for r in rows if n in blob(r)]
    if category.strip():
        n=category.strip().lower();rows=[r for r in rows if n in str(_property_category(_flat_record(r.get("clean_record")),r.get("transaction_type"))).lower()]
    if transaction.strip():
        n=transaction.strip().upper();rows=[r for r in rows if str(r.get("transaction_type") or _first(_flat_record(r.get("clean_record")),"transaction_type","rent_sale","rent_or_sale") or "").upper()==n]
    if status.strip():
        n=status.strip().upper();rows=[r for r in rows if n in {str(r.get("availability_status") or "").upper(),str(r.get("verification_status") or "").upper()}]
    if assigned.strip():
        n=assigned.strip().lower();rows=[r for r in rows if n in str(r.get("assigned_to") or "").lower()]
    return rows

def _requirement_rows(e,source,q,location,category,transaction,status,assigned,limit):
    pat=_src_pat(source)
    sc=""
    if pat:
        sc="""AND EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.canonical_id=r.canonical_id
        AND l.master_entity_type='REQUIREMENT' AND (UPPER(COALESCE(l.source_type,'')) LIKE :pat OR UPPER(COALESCE(l.source_table,'')) LIKE :pat))"""
    sql=f"""SELECT r.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
    COALESCE(w.assigned_to,a.assigned_to) assigned_to
    FROM pi_master_requirements_v711 r
    LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
    LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id
    WHERE 1=1 {sc}
    AND (:q='%%' OR r.canonical_id ILIKE :q OR COALESCE(r.locality,'') ILIKE :q OR COALESCE(r.city,'') ILIKE :q OR COALESCE(r.clean_record::text,'') ILIKE :q)
    AND (:loc='%%' OR COALESCE(r.locality,'') ILIKE :loc OR COALESCE(r.city,'') ILIKE :loc OR COALESCE(r.clean_record::text,'') ILIKE :loc)
    AND (:tx='' OR UPPER(COALESCE(r.transaction_type,''))=:tx)
    AND (:st='' OR UPPER(COALESCE(w.verification_status,'UNVERIFIED'))=:st)
    AND (:asgn='%%' OR COALESCE(w.assigned_to,a.assigned_to,'') ILIKE :asgn)
    ORDER BY r.updated_at DESC NULLS LAST,r.created_at DESC NULLS LAST LIMIT :n"""
    with e.connect() as c:
        rows=[dict(x) for x in c.execute(text(sql),{"q":f"%{q.strip()}%","loc":f"%{location.strip()}%","tx":transaction.upper().strip(),
        "st":status.upper().strip(),"asgn":f"%{assigned.strip()}%","n":limit,"pat":pat or ""}).mappings().all()]
    # Manual requirements created by the settled manual form are authoritative
    # evidence in the Requirement Gate even when an older Master source link is
    # absent. Surface those existing rows read-only in the Manual database.
    if source=="MANUAL" and not rows:
        try:
            with e.connect() as c:
                exists=c.execute(text("SELECT to_regclass('public.pi_requirement_gate_v1191')")).scalar()
                if exists:
                    raw=c.execute(text("""SELECT to_jsonb(g) AS d FROM pi_requirement_gate_v1191 g
                        WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED')
                          AND (UPPER(COALESCE(source_type,'')) LIKE '%%MANUAL%%'
                               OR UPPER(COALESCE(source_table,'')) LIKE '%%MANUAL%%')
                          AND (:q='%%' OR to_jsonb(g)::text ILIKE :q)
                        ORDER BY created_at DESC NULLS LAST,id DESC LIMIT :n"""),
                        {"q":f"%{q.strip()}%","n":limit}).scalars().all()
                    for x in raw:
                        d=x if isinstance(x,dict) else json.loads(x)
                        locs=d.get("locations") or []
                        if isinstance(locs,str):
                            try: locs=json.loads(locs)
                            except Exception: locs=[locs]
                        phones=d.get("contact_numbers") or []
                        if isinstance(phones,str):
                            try: phones=json.loads(phones)
                            except Exception: phones=[phones]
                        rows.append({
                            "canonical_id":str(d.get("id") or ""),
                            "locality":", ".join(map(str,locs)) if isinstance(locs,list) else str(locs or ""),
                            "city":"",
                            "transaction_type":d.get("transaction_type") or "",
                            "area_sqft":d.get("area_min_sqft") or d.get("area_max_sqft") or "",
                            "budget_raw":d.get("budget_max") or d.get("budget_min") or "",
                            "created_at":d.get("created_at"),
                            "verification_status":"VERIFIED" if "VERIFIED" in str(d.get("classification") or "").upper() and "NEEDS" not in str(d.get("classification") or "").upper() else "UNVERIFIED",
                            "assigned_to":"",
                            "clean_record":{
                                "requirement_text":d.get("original_message") or "",
                                "contact_phone":", ".join(map(str,phones)) if isinstance(phones,list) else str(phones or ""),
                                "location":", ".join(map(str,locs)) if isinstance(locs,list) else str(locs or ""),
                                "property_category":d.get("property_category") or d.get("intended_use") or "",
                                "property_type":d.get("property_category") or "",
                                "budget":d.get("budget_max") or d.get("budget_min") or "",
                                "source":"MANUAL",
                            },
                        })
        except Exception:
            pass
    if category.strip():
        want=category.strip().lower()
        rows=[r for r in rows if want in str(_first(_dict(r.get("clean_record")),"property_category","category","required_property_category") or "").lower()]
    return rows
def _shell(title,body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial;color:#172033}}header{{background:#0d2238;color:white;padding:16px 20px}}
nav{{background:white;border-bottom:1px solid #667085;padding:8px;display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;z-index:10}}
nav a,.btn,button,.summarybtn{{background:#0d2238;color:white;text-decoration:none;border:1px solid #0d2238;padding:7px 9px;cursor:pointer;font-size:12px;border-radius:2px}}
.good{{background:#067647!important;border-color:#067647!important}}.light{{background:#475467!important}}.danger{{background:#b42318!important}}
.wrap{{max-width:2100px;margin:auto;padding:14px}}.card{{background:white;border:1px solid #98a2b3;padding:10px;margin-bottom:10px}}
.searchgrid{{display:grid;grid-template-columns:2fr repeat(6,minmax(130px,1fr));gap:6px}}input,select{{width:100%;padding:7px;border:1px solid #98a2b3;border-radius:0}}
.tablebox{{overflow:auto;max-height:76vh;border:1px solid #667085;background:white}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px;table-layout:auto}}
th,td{{border:1px solid #98a2b3;padding:6px 7px;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:break-word;word-break:normal;min-width:95px;max-width:240px}}
th{{background:#e9eef5;position:sticky;top:0;z-index:4;white-space:nowrap;min-width:110px}}
tbody tr:nth-child(even) td{{background:#f8fafc}}tbody tr:hover td{{background:#eef4ff}}
.desc{{min-width:320px!important;max-width:460px!important;white-space:normal!important}}
.loc{{min-width:160px!important;max-width:260px!important;white-space:normal!important}}
.nowrap{{white-space:nowrap!important;min-width:120px}}
td:nth-child(10),td:nth-child(11){{min-width:140px;max-width:190px}}
td:nth-child(12){{min-width:150px;max-width:190px}}
td:nth-child(14),td:nth-child(15),td:nth-child(18),td:nth-child(19){{min-width:90px;max-width:130px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}}.dbcard{{border:1px solid #98a2b3;background:white;padding:12px}}.dbcard h3{{margin:0 0 5px}}
details.pop{{position:relative}}details.pop>div{{position:absolute;z-index:20;background:white;border:1px solid #667085;padding:9px;min-width:430px}}details.pop summary{{list-style:none}}
.dbtools{{display:flex;align-items:center;gap:5px;margin:0 0 7px;background:#fff;padding:5px;border:1px solid #d0d5dd;width:max-content;position:sticky;left:0;z-index:6}}.dbtools button{{padding:4px 7px}}
body.compact table{{font-size:10px}}body.compact th,body.compact td{{padding:4px 5px}}body.compact .desc{{min-width:220px;max-width:330px}}
@media(max-width:1000px){{.searchgrid{{grid-template-columns:1fr 1fr}}}}
</style><script>
function dbApplyZoom(){{var z=Number(localStorage.getItem('allianceDbZoom')||100);document.querySelectorAll('.tablebox table').forEach(function(t){{t.style.setProperty('font-size',(11*z/100)+'px','important')}});document.querySelectorAll('.tablebox th,.tablebox td').forEach(function(x){{x.style.setProperty('padding',(6*z/100)+'px','important')}})}}
function dbZoom(d){{var z=Number(localStorage.getItem('allianceDbZoom')||100);z=Math.max(70,Math.min(160,z+d*10));localStorage.setItem('allianceDbZoom',z);dbApplyZoom()}}
function dbZoomReset(){{localStorage.setItem('allianceDbZoom',100);dbApplyZoom()}}
function dbCompact(){{document.body.classList.toggle('compact');localStorage.setItem('allianceDbCompact',document.body.classList.contains('compact')?'1':'0')}}
document.addEventListener('DOMContentLoaded',function(){{if(localStorage.getItem('allianceDbCompact')==='1')document.body.classList.add('compact');dbApplyZoom()}})
</script></head><body><header><b>Alliance CRE Operating System</b><br><small>5 Property Databases + 5 Requirement Databases · Master-only Matcher</small></header>
<nav><a href="#" onclick="history.back();return false">← Back to Previous Page</a><a href="/team-dashboard-v376">Back to Dashboard</a></nav>
<div class="wrap"><h2>{_e(title)}</h2>{body}</div></body></html>"""
def _filter_form(q,location,category,transaction,status,assigned,limit):
    cats="<option value=''>All Categories</option>"+"".join(f"<option {'selected' if category==x else ''}>{_e(x)}</option>" for x in CATEGORY_OPTIONS)
    return f"""<div class="card"><form class="searchgrid">
    <input name="q" value="{_e(q)}" placeholder="Search ID, description, contact, source">
    <input name="location" value="{_e(location)}" placeholder="Location">
    <select name="category">{cats}</select>
    <select name="transaction"><option value="">Rent / Sale</option><option value="RENT" {'selected' if transaction.upper()=='RENT' else ''}>Rent</option><option value="SALE" {'selected' if transaction.upper()=='SALE' else ''}>Sale</option><option value="LEASE" {'selected' if transaction.upper()=='LEASE' else ''}>Lease</option></select>
    <select name="status"><option value="">All Status</option><option {'selected' if status.upper()=='AVAILABLE' else ''}>AVAILABLE</option><option {'selected' if status.upper()=='UNVERIFIED' else ''}>UNVERIFIED</option><option {'selected' if status.upper()=='VERIFIED' else ''}>VERIFIED</option></select>
    <input name="assigned" value="{_e(assigned)}" placeholder="Assigned To">
    <input type="number" name="limit" min="1" max="1500" value="{limit}">
    <button>Search</button></form></div>"""
def _property_table(core,e,req,source,q,location,category,transaction,status,assigned,limit):
    rows=_property_rows(e,source,q,location,category,transaction,status,assigned,limit)
    trs=[]
    for r in rows:
        cr=_flat_record(r.get("clean_record"));cid=str(r["canonical_id"])
        locality=_clean_location_value(r.get("locality") or _first(cr,"location","locality") or "",cr)
        if locality=="Needs verification":continue
        address=_first(cr,"address","exact_address","property_address") or ""
        desc=_first(cr,"description","property_description","original_description","source_text","raw_line","original_message","details","team_description","description_edit")
        if not desc:
            parts=[]
            for label,keys in (("Property",("property_name","property_type","property_types")),("Address",("address","exact_address","property_address")),
                ("Area",("area_text","area_display","area","available_area","area_sqft")),("Floor",("floor","floors")),
                ("Suitable",("suitable_for","property_category","category")),("Parking",("parking","parking_details")),
                ("Possession",("possession","possession_status")),("Remarks",("remarks","additional_points"))):
                v=_first(cr,*keys)
                if v not in (None,"",[],{}):parts.append(label+": "+_display_text(v))
            desc=" | ".join(parts)
        desc=_display_text(desc)
        if address and address.lower() not in desc.lower():desc=(address+" · "+desc).strip(" ·")
        tx=r.get("transaction_type") or _first(cr,"transaction_type","rent_or_sale") or ""
        pcat=_property_category(cr,tx)
        ptype=_first(cr,"property_type","property_types","asset_type","subtype") or ""
        ptype=_display_text(ptype)
        area=_first(cr,"area_text","area_display","available_area")
        if not area:
            av=_first(cr,"area_value","area_sqft","area","size") or r.get("area_sqft") or ""
            au=_first(cr,"area_unit") or ("SQFT" if r.get("area_sqft") else "")
            area=f"{av} {au}".strip()
        floor=_first(cr,"floor","floors","floor_codes") or ""
        if isinstance(floor,list):floor=", ".join(map(str,floor))
        amount=_first(cr,"rent_text","amount","amount_raw","price_raw")
        if not amount:
            amount=_first(cr,"rent","monthly_rent","rent_amount","rent_in_figures") if str(tx).upper() in ("RENT","LEASE") else _first(cr,"sale_price","sale_amount","price","asking_price")
        amount=amount or r.get("price_raw") or ""
        cname,cphone=_contacts(cr)
        stat=r.get("availability_status")
        if not stat or stat=="UNKNOWN":stat=r.get("verification_status") or "UNVERIFIED"
        source_name=_source_name(e,cid,"PROPERTY") or str(_first(cr,"entry_source","source","source_type") or source)
        source_only="-SOURCE-" in cid
        if source_only:
            verify="Source record";history="Source only";edit="Source source";delete="Source only"
        else:
            verify=f"""<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/primary/property/{_e(cid)}/verify">
            <select name="status" required><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>CALL_BACK</option><option>SOLD</option><option>RENTED</option><option>HOLD</option><option>WRONG_NUMBER</option></select>
            <select name="verified_with" required><option>OWNER</option><option>BROKER</option><option>OTHER</option></select>
            <input name="verified_by" required placeholder="Verified By team member"><input name="remarks" placeholder="Remarks"><input type="datetime-local" name="next_verification_at"><button class="good">Save</button></form></div></details>"""
            history=f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}">History</a>'
            edit=f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}/edit">Edit</a>'
            delete=f"""<form method="post" action="/alliance/primary/property/{_e(cid)}/delete" onsubmit="return confirm('Archive this property? Original source evidence remains preserved.');"><button class="danger">Delete</button></form>"""
        vals=[cid,locality,desc,pcat,ptype,area,floor,tx,amount,cname,cphone,_fmt_dt(r.get("created_at")),stat,verify,history,r.get("assigned_to") or "",source_name,edit,delete]
        cls=["nowrap","loc","desc","","","","","nowrap","","","","nowrap","nowrap","","","","","",""]
        trs.append("<tr>"+"".join(f'<td class="{cls[i]}">{x if i in (13,14,17,18) else _e(_shown(x))}</td>' for i,x in enumerate(vals))+"</tr>")
    H=["Property ID","Location","Description / Address","Property Category","Property Type","Area","Floor","Rent/Sale","Amount","Contact Name","Contact No.","Date & Time","Status","Verify","History","Assigned To","Source","Edit","Delete"]
    return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="dbtools"><b>Table</b><button type="button" onclick="dbCompact()">Compact</button><button type="button" onclick="dbZoom(-1)">−</button><button type="button" onclick="dbZoom(1)">+</button><button type="button" onclick="dbZoomReset()">Reset</button></div><div class="tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=19>No records found</td></tr>"}</tbody></table></div>'

def _requirement_table(e,source,q,location,category,transaction,status,assigned,limit):
    rows=_requirement_rows(e,source,q,location,category,transaction,status,assigned,limit)
    trs=[]
    for r in rows:
        cr=_dict(r.get("clean_record")); cid=str(r["canonical_id"])
        desc=_first(cr,"requirement_text","original_message","original_description","additional_points","description") or ""
        company=_first(cr,"company_name","brand_name","client_company","company") or ""
        cname=_first(cr,"contact_name","client_name","name") or ""
        phone=_first(cr,"contact_phone","contact_number","phone","mobile") or ""
        loc=r.get("locality") or _first(cr,"location","locality") or ""
        pcat=_first(cr,"property_category","required_property_category","category") or ""
        ptype=_first(cr,"property_type","required_property_type","asset_type") or ""
        area=_first(cr,"required_area","required_area_sqft","minimum_area_sqft","maximum_area_sqft") or r.get("area_sqft") or ""
        tx=r.get("transaction_type") or _first(cr,"transaction_type","rent_or_sale") or ""
        budget=_first(cr,"budget","rent_budget","sale_budget","budget_raw") or r.get("budget_raw") or ""
        st=r.get("verification_status") or "UNVERIFIED"
        src=_source_name(e,cid,"REQUIREMENT")
        vals=[cid,desc,company,cname,phone,loc,pcat,ptype,area,tx,budget,_fmt_dt(r.get("created_at")),st,r.get("assigned_to") or "",src,
              f'<a class="btn light" href="/alliance/primary/requirement/{_e(cid)}">Open</a>']
        trs.append("<tr>"+"".join(f'<td class="{"desc" if i==1 else ""}">{x if i==15 else _e(_shown(x))}</td>' for i,x in enumerate(vals))+"</tr>")
    H=["Requirement ID","Requirement / Original Message","Client / Company","Contact Name","Contact No.","Location","Property Category","Property Type","Area","Rent/Sale","Budget","Date & Time","Status","Assigned To","Source","Open"]
    return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="dbtools"><b>Table</b><button type="button" onclick="dbCompact()">Compact</button><button type="button" onclick="dbZoom(-1)">−</button><button type="button" onclick="dbZoom(1)">+</button><button type="button" onclick="dbZoomReset()">Reset</button></div><div class="tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=16>No requirements found</td></tr>"}</tbody></table></div>'
def register(core, served_app=None):
    app=served_app or _app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("Known-good Property Authority requires app + engine")
    @app.get("/alliance/primary/databases")
    def canonical_property_databases(req:Request):
        _login(core,req);return RedirectResponse("/alliance/final/databases",307)

    @app.get("/alliance/final/databases",response_class=HTMLResponse)
    def dbhub(req:Request):
        _login(core,req)
        cards="".join(
            f'<div class="dbcard"><h3>{s.title()} Database</h3>'
            f'<p>{("All verified and canonical properties from every source. This is the only matcher inventory." if s=="MASTER" else "Search this source database; source evidence remains preserved.")}</p>'
            f'<a class="btn good" href="/alliance/final/database/{s.lower()}">Open & Search</a></div>'
            for s in SOURCES
        )
        actions="""<div class="card"><b>Add Property by Source</b><br><br>
        <a class="btn good" href="/property-manual">+ Add Manual Property</a>
        <a class="btn good" href="/newspaper-upload">+ Upload Newspaper</a>
        <a class="btn good" href="/magazine-capture">+ Upload Magazine</a>
        <a class="btn good" href="/whatsapp-live">Open WhatsApp Live</a></div>"""
        return HTMLResponse(_shell("5 Property Databases",actions+f'<div class="grid">{cards}</div>'))

    @app.get("/alliance/final/database/{source}",response_class=HTMLResponse)
    def db(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        _login(core,req);src=source.upper()
        if src not in SOURCES:return HTMLResponse("Unknown property database",404)
        return HTMLResponse(_shell(f"{src.title()} Property Database",_property_table(core,e,req,src,q,location,category,transaction,status,assigned,limit)))
    return {"status":"REGISTERED","version":VERSION,"scope":"PROPERTY_ONLY","requirements_registered":False}
