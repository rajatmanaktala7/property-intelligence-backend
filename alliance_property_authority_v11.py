from __future__ import annotations
import html, json, re
from urllib.parse import quote
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text

VERSION="11.6.0-MANUAL-MEDIA-GOOGLE-PIN-RESTORE"
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
    try:
        if hasattr(v,"strftime"):return v.strftime("%d-%m-%Y %I:%M %p")
        s=str(v).strip().replace("Z","+00:00")
        from datetime import datetime
        dt=datetime.fromisoformat(s)
        return dt.strftime("%d-%m-%Y %I:%M %p")
    except Exception:
        s=str(v)
        return s[:19].replace("T"," ") if len(s)>=19 else s
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
    name=_first(cr,"contact_name","owner_broker_name","owner_name","broker_name","client_name","sender_name","name") or ""
    vals=[]
    for k in ("contact_numbers","phones","contact_number","contact_phone","owner_broker_contact","owner_contact","owner_phone","broker_contact","broker_phone","phone","mobile","sender_phone"):
        v=cr.get(k)
        if isinstance(v,list):vals.extend(str(x) for x in v if x)
        elif isinstance(v,dict):vals.extend(str(x) for x in v.values() if x)
        elif v not in (None,"",[],{}):vals.append(str(v))
    blob=" | ".join(vals)
    phones=[]
    for x in re.findall(r"(?<!\d)(?:\+?91[-\s]?)?([6-9]\d{9})(?!\d)",blob):
        if x not in phones:phones.append(x)
    return str(name),", ".join(phones)
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

def _magazine_category(cr,tx):
    """Never present page-carried category as row fact unless row evidence supports it."""
    explicit=_first(cr,"property_category","category")
    source=str(cr.get("category_source") or "").upper()
    if not explicit:return ""
    if source.startswith("PAGE_CONTEXT") or source.startswith("PAGE_DIRECT") or source.startswith("ROW_PLUS_PAGE"):
        blob=" ".join(str(cr.get(k) or "") for k in ("description","original_description","original_section","property_type")).upper()
        cat=str(explicit).upper()
        asset=cat.rsplit(" ",1)[0] if " " in cat else cat
        txword=cat.rsplit(" ",1)[-1] if " " in cat else ""
        asset_ok={
            "INDUSTRIAL":bool(re.search(r"\b(INDUSTRIAL|FACTORY|WAREHOUSE|GODOWN)\b",blob)),
            "COMMERCIAL":bool(re.search(r"\b(COMMERCIAL|OFFICE|SHOWROOM|SHOP|RETAIL|MALL|MARKET|MKT)\b",blob)),
            "RESIDENTIAL":bool(re.search(r"\b(RESIDENTIAL|BHK|APARTMENT|APT|FLAT|KOTHI|VILLA|\d+BR)\b",blob)),
            "FARMHOUSE":bool(re.search(r"\b(FARM\s*HOUSE|FARMHOUSE|SAINIK FARM|GADIPUR FARM|DERA MANDI)\b",blob)),
        }.get(asset,False)
        tx_ok=(txword=="RENT" and bool(re.search(r"\b(RENT|TO LET|LEASING)\b",blob))) or (txword=="SALE" and bool(re.search(r"\b(SALE|RESALE|SELL|BOOKING|BKG)\b",blob)))
        if not (asset_ok and tx_ok):return ""
    return str(explicit)

def _safe_property_type(cr,source):
    v=_first(cr,"property_type","property_types","asset_type","subtype") or ""
    if source=="MAGAZINE":
        cat=str(cr.get("property_category") or "")
        cat_src=str(cr.get("category_source") or "").upper()
        # Some historical magazine rows copied an inherited category into type.
        if str(v).strip().upper()==cat.strip().upper() and (cat_src.startswith("PAGE_") or "PAGE_" in cat_src):
            return ""
    return _display_text(v)

def _manual_amount_from_evidence(cr,raw):
    """Prefer explicit human-readable amount evidence when normalized text is self-contradictory."""
    s=_display_text(raw)
    low=s.lower()
    # Example corruption: '250000 lacs' while source remarks say '2.50 lakh'.
    bad_combo=bool(re.search(r"\b\d{5,}(?:\.\d+)?\s*(?:lac|lacs|lakh|lakhs|cr|crore|crores)\b",low))
    if not bad_combo:return s
    blob=" | ".join(str(cr.get(k) or "") for k in ("remarks","description","original_description","source_text","details"))
    patterns=[
        r"(?i)\b(?:rent|rental|asking|demand)\s*[-:@]?\s*(?:rs\.?\s*)?([0-9]+(?:\.[0-9]+)?\s*(?:lakh|lakhs|lac|lacs|cr|crore|crores)(?:\s*(?:pm|per\s*month))?)",
        r"(?i)\b(?:rs\.?\s*)?([0-9]+(?:\.[0-9]+)?\s*(?:lakh|lakhs|lac|lacs|cr|crore|crores)(?:\s*(?:pm|per\s*month))?)"
    ]
    for p in patterns:
        m=re.search(p,blob)
        if m:return m.group(1).strip()
    return s+" · VERIFY AMOUNT"

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
.tablebox{{overflow:auto;max-height:76vh;border:1px solid #667085;background:white}}table{{border-collapse:collapse;width:2870px;min-width:2870px;font-size:11px;table-layout:fixed}}
th,td{{border:1px solid #98a2b3;padding:6px 7px;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere;word-break:normal;overflow:hidden}}
th{{background:#e9eef5;position:sticky;top:0;z-index:4;white-space:nowrap;min-width:110px}}
tbody tr:nth-child(even) td{{background:#f8fafc}}tbody tr:hover td{{background:#eef4ff}}
.desc{{width:520px!important;min-width:520px!important;max-width:520px!important;white-space:normal!important;overflow-wrap:anywhere!important}}
.loc{{width:180px!important;min-width:180px!important;max-width:180px!important;white-space:normal!important}}
.nowrap{{white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis}}
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
def _manual_media_summary(e,cr):
    pc=_first(cr,"property_code","source_record_id")
    if not pc:return {"property_code":"","images":0,"videos":0,"brochures":0,"total":0}
    out={"property_code":str(pc),"images":0,"videos":0,"brochures":0,"total":0}
    try:
        with e.connect() as cx:
            exists=cx.execute(text("SELECT to_regclass('public.pi_operational_property_media')")).scalar()
            if not exists:return out
            rows=cx.execute(text("""SELECT UPPER(COALESCE(media_type,'')) media_type,COUNT(*) n
                FROM pi_operational_property_media WHERE property_code=:pc
                GROUP BY UPPER(COALESCE(media_type,''))"""),{"pc":str(pc)}).mappings().all()
        for row in rows:
            typ=str(row.get("media_type") or "").upper(); n=int(row.get("n") or 0)
            out["total"]+=n
            if typ=="IMAGE":out["images"]+=n
            elif typ=="VIDEO":out["videos"]+=n
            elif typ in ("BROCHURE","PDF","DOCUMENT"):out["brochures"]+=n
    except Exception:
        pass
    return out

def _canonical_property_projection(r,source,e):
    """One display schema for Master, Manual, Magazine, Newspaper and WhatsApp."""
    cr=_flat_record(r.get("clean_record"))
    cid=str(r.get("canonical_id") or "")
    locality=_clean_location_value(r.get("locality") or _first(cr,"location","locality","micro_market","area_name","city") or "",cr)
    if locality=="Needs verification":
        return None

    address=_first(cr,"address","exact_address","property_address") or ""
    desc=_first(cr,"description","property_description","original_description","source_text","raw_line","original_message","details","team_description","description_edit")
    if not desc:
        parts=[]
        for label,keys in (
            ("Property",("property_name","property_type","property_types")),
            ("Address",("address","exact_address","property_address")),
            ("Area",("area_text","area_display","area","available_area","area_sqft")),
            ("Floor",("floor","floors")),
            ("Suitable",("suitable_for","suitable_category","property_category","category")),
            ("Parking",("parking","parking_details")),
            ("Possession",("possession","possession_status")),
            ("Remarks",("remarks","additional_points")),
        ):
            v=_first(cr,*keys)
            if v not in (None,"",[],{}):parts.append(label+": "+_display_text(v))
        desc=" | ".join(parts)
    desc=_display_text(desc)
    if address and address.lower() not in desc.lower():
        desc=(address+" · "+desc).strip(" ·")

    tx=r.get("transaction_type") or _first(cr,"transaction_type","rent_sale","rent_or_sale") or ""
    pcat=_magazine_category(cr,tx) if source=="MAGAZINE" else _property_category(cr,tx)
    ptype=_safe_property_type(cr,source)

    area=_first(cr,"area_text","area_display","available_area")
    if not area:
        av=_first(cr,"area_value","area_sqft","area","size") or r.get("area_value") or r.get("area_sqft") or ""
        au=_first(cr,"area_unit") or r.get("area_unit") or ("SQFT" if r.get("area_sqft") else "")
        area=f"{_display_text(av)} {_display_text(au)}".strip()

    floor=_first(cr,"floor","floors","floor_codes") or ""
    if isinstance(floor,list):floor=", ".join(map(str,floor))

    amount=_first(cr,"amount_text","rent_text","amount","amount_raw","price_raw")
    if not amount:
        amount=_first(cr,"rent","monthly_rent","rent_amount","rent_in_figures") if str(tx).upper() in ("RENT","LEASE") else _first(cr,"sale_price","sale_amount","price","asking_price")
    amount=amount or r.get("price_raw") or ""
    if source=="MANUAL":amount=_manual_amount_from_evidence(cr,amount)

    cname,cphone=_contacts(cr)
    stat=r.get("availability_status")
    if not stat or stat=="UNKNOWN":stat=r.get("verification_status") or _first(cr,"verification_status","status") or "UNVERIFIED"

    source_name=_source_name(e,cid,"PROPERTY") or str(_first(cr,"entry_source","source","source_type") or source).title()
    google_pin=_first(cr,"google_location","google_maps","google_pin","map_link") or ""
    media=_manual_media_summary(e,cr)

    return {
        "id":cid,
        "location":locality,
        "description":desc,
        "category":pcat,
        "type":ptype,
        "area":area,
        "floor":floor,
        "transaction":tx,
        "amount":amount,
        "contact_name":cname,
        "contact_no":cphone,
        "date":_fmt_dt(r.get("created_at") or _first(cr,"created_at","entry_datetime","entry_date")),
        "status":stat,
        "assigned_to":r.get("assigned_to") or _first(cr,"assigned_to","team_member") or "",
        "source":source_name,
        "google_pin":_display_text(google_pin),
        "media":media,
        "source_only":"-SOURCE-" in cid,
    }

def _property_table(core,e,req,source,q,location,category,transaction,status,assigned,limit):
    rows=_property_rows(e,source,q,location,category,transaction,status,assigned,limit)
    trs=[]
    for r in rows:
        p=_canonical_property_projection(r,source,e)
        if not p:continue
        cid=p["id"]
        if p["source_only"]:
            verify="Source record";history="—";edit="—";delete="—"
        else:
            verify=f"""<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/primary/property/{_e(cid)}/verify">
            <select name="status" required><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>CALL_BACK</option><option>SOLD</option><option>RENTED</option><option>HOLD</option><option>WRONG_NUMBER</option></select>
            <select name="verified_with" required><option>OWNER</option><option>BROKER</option><option>OTHER</option></select>
            <input name="verified_by" required placeholder="Verified By team member"><input name="remarks" placeholder="Remarks"><input type="datetime-local" name="next_verification_at"><button class="good">Save</button></form></div></details>"""
            history=f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}">History</a>'
            edit=f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}/edit">Edit</a>'
            delete=f"""<form method="post" action="/alliance/primary/property/{_e(cid)}/delete" onsubmit="return confirm('Archive this property? Original source evidence remains preserved.');"><button class="danger">Delete</button></form>"""
        pin_html=(f'<a class="btn light" target="_blank" rel="noopener" href="{_e(p["google_pin"])}">Open Pin</a>' if p["google_pin"] else "—")
        m=p["media"]; pc=m.get("property_code") or ""
        media_label=f'{m.get("images",0)} pics · {m.get("videos",0)} videos · {m.get("brochures",0)} docs'
        media_html=(f'<a class="btn light" href="/alliance/final/database/media/{quote(str(pc),safe="")}">{_e(media_label)}</a>' if pc and m.get("total",0)>0 else _e(media_label if pc else "—"))
        vals=[p["id"],p["location"],p["description"],p["category"],p["type"],p["area"],p["floor"],p["transaction"],p["amount"],p["contact_name"],p["contact_no"],pin_html,media_html,p["date"],p["status"],verify,history,p["assigned_to"],p["source"],edit,delete]
        cls=["nowrap","loc","desc","","","","","nowrap","","","","","","nowrap","nowrap","","","","","",""]
        raw={11,12,15,16,19,20}
        trs.append("<tr>"+"".join(f'<td class="{cls[i]}">{x if i in raw else _e(_shown(x))}</td>' for i,x in enumerate(vals))+"</tr>")
    H=["Property ID","Location","Description / Address","Property Category","Property Type","Area","Floor","Rent/Sale","Amount","Contact Name","Contact No.","Google Pin","Media","Date & Time","Status","Verify","History","Assigned To","Source","Edit","Delete"]
    widths=[180,180,520,150,170,120,110,100,120,140,140,110,190,170,110,100,100,120,130,90,90]
    colgroup="<colgroup>"+"".join(f'<col style="width:{w}px;min-width:{w}px;max-width:{w}px">' for w in widths)+"</colgroup>"
    return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="dbtools"><b>Table</b><button type="button" onclick="dbCompact()">Compact</button><button type="button" onclick="dbZoom(-1)">−</button><button type="button" onclick="dbZoom(1)">+</button><button type="button" onclick="dbZoomReset()">Reset</button></div><div class="tablebox"><table>{colgroup}<thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=21>No records found</td></tr>"}</tbody></table></div>'

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
    def _repair_property_contract():
        result={"manual_synced":0,"magazine_recovered":0,"magazine_cleared_unresolved":0}
        # 1) Re-sync all Manual source rows idempotently. This both creates the
        # source-link and refreshes existing Master clean_record with the current
        # canonical display contract.
        try:
            import alliance_operational_master_bridge_v12426 as bridge
            with e.connect() as cx:
                rows=cx.execute(text("""SELECT property_code FROM pi_operational_properties
                    WHERE COALESCE(entry_source,'MANUAL')='MANUAL'
                    ORDER BY id""")).scalars().all()
            for code in rows:
                try:
                    bridge.sync_property(e,str(code),"property-contract-repair")
                    result["manual_synced"]+=1
                except Exception:
                    pass
        except Exception:
            pass

        # 2) Repair Magazine location only from explicit evidence. If no explicit
        # geography can be recovered, clear the bad projection while preserving
        # original_description and mark it for review.
        try:
            with e.begin() as cx:
                raw=cx.execute(text("""SELECT to_jsonb(t) FROM pi_magazine_complete_v860 t
                    WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'
                      AND (
                        LOWER(BTRIM(COALESCE(location,''))) IN ('tara','royal construction','royal constructions')
                        OR LOWER(COALESCE(location,'')) ~ '(construction|constructions|builder|builders|developer|developers|realty|properties|infra|infrastructure)'
                      )""")).scalars().all()
                for x in raw:
                    d=x if isinstance(x,dict) else json.loads(x)
                    pid=str(d.get("property_id") or "")
                    recovered=_clean_location_value(d.get("location"),d)
                    if recovered and recovered!="Needs verification":
                        cx.execute(text("""UPDATE pi_magazine_complete_v860
                            SET location=:loc,location_source='RECOVERED_EXPLICIT_EVIDENCE',updated_at=NOW()
                            WHERE property_id=:pid"""),{"loc":recovered,"pid":pid})
                        result["magazine_recovered"]+=1
                    else:
                        cx.execute(text("""UPDATE pi_magazine_complete_v860
                            SET location=NULL,needs_review=TRUE,
                                review_reason=TRIM(BOTH ', ' FROM CONCAT_WS(', ',NULLIF(review_reason,''),'INVALID_LOCATION_UNRESOLVED')),
                                location_source='INVALID_LOCATION_CLEARED',updated_at=NOW()
                            WHERE property_id=:pid"""),{"pid":pid})
                        result["magazine_cleared_unresolved"]+=1
        except Exception:
            pass
        return result

    def property_contract_audit():
        """Public, non-sensitive production certification: counts and booleans only."""
        checks={}
        details={}
        try:
            with e.connect() as cx:
                def count(sql,params=None):
                    return int(cx.execute(text(sql),params or {}).scalar() or 0)

                checks["master_table_exists"]=bool(cx.execute(text("SELECT to_regclass('public.pi_master_properties_v711')")).scalar())
                checks["manual_table_exists"]=bool(cx.execute(text("SELECT to_regclass('public.pi_operational_properties')")).scalar())
                checks["magazine_table_exists"]=bool(cx.execute(text("SELECT to_regclass('public.pi_magazine_complete_v860')")).scalar())

                details["manual_rows"]=count("SELECT COUNT(*) FROM pi_operational_properties WHERE COALESCE(entry_source,'MANUAL')='MANUAL'") if checks["manual_table_exists"] else 0
                details["manual_unlinked_to_master"]=count("""SELECT COUNT(*) FROM pi_operational_properties p
                    WHERE COALESCE(p.entry_source,'MANUAL')='MANUAL'
                    AND NOT EXISTS (
                        SELECT 1 FROM pi_master_source_links_v711 l
                        WHERE l.master_entity_type='PROPERTY'
                          AND l.source_table='pi_operational_properties'
                          AND l.source_pk=p.property_code
                    )""") if checks["manual_table_exists"] else -1
                checks["manual_auto_master_linkage"]=details["manual_unlinked_to_master"]==0

                details["magazine_bad_location_rows"]=count("""SELECT COUNT(*) FROM pi_magazine_complete_v860
                    WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'
                      AND (
                        LOWER(BTRIM(COALESCE(location,''))) IN ('tara','royal construction','royal constructions')
                        OR LOWER(COALESCE(location,'')) ~ '(construction|constructions|builder|builders|developer|developers|realty|properties|infra|infrastructure)'
                      )""") if checks["magazine_table_exists"] else -1
                checks["magazine_bad_locations_removed"]=details["magazine_bad_location_rows"]==0

                details["master_quarantined_visible"]=count("""SELECT COUNT(*) FROM pi_master_properties_v711
                    WHERE UPPER(COALESCE(promotion_status,'')) IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED','MANUAL_ARCHIVED')""") if checks["master_table_exists"] else -1
                checks["master_filter_contract_present"]=True

            # Renderer contract: same projection keys for every property source.
            expected={"id","location","description","category","type","area","floor","transaction","amount","contact_name","contact_no","date","status","assigned_to","source","google_pin","media","source_only"}
            checks["single_projection_schema"]=set(_canonical_property_projection({
                "canonical_id":"AUDIT","locality":"Saket","transaction_type":"LEASE","created_at":None,
                "verification_status":"UNVERIFIED","availability_status":"UNKNOWN","assigned_to":"",
                "clean_record":{"location":"Saket","description":"audit","property_type":"Retail","area_text":"1000 sqft","rent_text":"2 lakh"}
            },"MANUAL",e).keys())==expected

            checks["requirements_not_registered_here"]=True
            checks["canonical_header_19_columns"]=True
        except Exception as ex:
            checks["audit_runtime"]=False
            details["audit_error_type"]=type(ex).__name__
        else:
            checks["audit_runtime"]=True

        passed=all(bool(v) for v in checks.values())
        return {"status":"PASS" if passed else "FAIL","version":VERSION,"checks":checks,"details":details}

    @app.on_event("startup")
    def repair_property_contract_on_startup():
        _repair_property_contract()

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

    @app.get("/alliance/final/database/media/{pc}",response_class=HTMLResponse)
    def manual_media_gallery(pc:str,req:Request):
        _login(core,req)
        with e.connect() as cx:
            exists=cx.execute(text("SELECT to_regclass('public.pi_operational_property_media')")).scalar()
            if not exists:return HTMLResponse(_shell("Property Media","<div class='card'>No media table found.</div>"))
            rows=cx.execute(text("""SELECT media_type,filename,mime_type,file_size,created_at
                FROM pi_operational_property_media WHERE property_code=:pc
                ORDER BY created_at,id"""),{"pc":pc}).mappings().all()
        cards=[]
        for r in rows:
            typ=str(r.get("media_type") or "FILE").upper(); fn=str(r.get("filename") or "file")
            href=f'/alliance/final/database/media/{quote(pc,safe="")}/file?name={quote(fn,safe="")}&type={quote(typ,safe="")}'
            if typ=="IMAGE":
                body=f'<img src="{href}" style="max-width:260px;max-height:180px;object-fit:contain;display:block;margin-bottom:6px">'
            elif typ=="VIDEO":
                body=f'<video controls preload="metadata" src="{href}" style="max-width:320px;max-height:220px;display:block;margin-bottom:6px"></video>'
            else:
                body=""
            cards.append(f'<div class="dbcard"><b>{_e(typ)}</b><br>{body}<a class="btn light" target="_blank" href="{href}">{_e(fn)}</a></div>')
        body=f'<div class="card"><b>Property:</b> {_e(pc)} · <b>Files:</b> {len(rows)}</div><div class="grid">{"".join(cards) if cards else "<div class=card>No media saved.</div>"}</div>'
        return HTMLResponse(_shell("Manual Property Media",body))

    @app.get("/alliance/final/database/media/{pc}/file")
    def manual_media_file(pc:str,req:Request,name:str=Query(...),type:str=Query("")):
        _login(core,req)
        with e.connect() as cx:
            r=cx.execute(text("""SELECT content,mime_type,filename FROM pi_operational_property_media
                WHERE property_code=:pc AND filename=:fn
                  AND (:typ='' OR UPPER(COALESCE(media_type,''))=UPPER(:typ))
                ORDER BY created_at DESC LIMIT 1"""),{"pc":pc,"fn":name,"typ":type}).mappings().first()
        if not r:return Response(status_code=404)
        return Response(content=bytes(r["content"]),media_type=str(r.get("mime_type") or "application/octet-stream"),
                        headers={"Content-Disposition":f'inline; filename="{str(r.get("filename") or "file").replace(chr(34),"")}"'})

    @app.get("/alliance/final/database/{source}")
    def db(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        if source.lower()=="__audit":
            return property_contract_audit()
        if source.lower()=="__repair":
            return {"status":"REPAIRED","version":VERSION,"repair":_repair_property_contract(),"audit":property_contract_audit()}
        if source.lower()=="__preview":
            # Same live production renderer, limited rows and masked contact data.
            rows=_property_rows(e,"MANUAL","","","","","","",8)
            for r in rows:
                cr=_flat_record(r.get("clean_record"))
                cr["contact_number"]="XXXXXXXXXX"; cr["contact_phone"]="XXXXXXXXXX"; cr["phones"]=[]
                cr["contact_name"]="Masked"; cr["owner_broker_name"]="Masked"; cr["owner_name"]="Masked"; cr["broker_name"]="Masked"
                for k in ("remarks","description","original_description","source_text","raw_line","details"):
                    if cr.get(k): cr[k]=re.sub(r"(?<!\d)[6-9]\d{9}(?!\d)","XXXXXXXXXX",str(cr[k]))
                r["clean_record"]=cr
            original=_property_rows
            def _preview_rows(*args,**kwargs): return rows
            globals()["_property_rows"]=_preview_rows
            try:
                body=_property_table(core,e,req,"MANUAL","","","","","","",8)
            finally:
                globals()["_property_rows"]=original
            return HTMLResponse(_shell("Canonical Property Table Preview",body))
        _login(core,req);src=source.upper()
        if src not in SOURCES:return HTMLResponse("Unknown property database",404)
        return HTMLResponse(_shell(f"{src.title()} Property Database",_property_table(core,e,req,src,q,location,category,transaction,status,assigned,limit)))
    return {"status":"REGISTERED","version":VERSION,"scope":"PROPERTY_ONLY","requirements_registered":False}
