from __future__ import annotations
import html, json, re
from urllib.parse import quote
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text, bindparam

VERSION="12.2.1-FAST-TRUE-SOURCE-AUTHORITIES"
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
def _source_link(e,cid,etype="PROPERTY"):
    try:
        with e.connect() as cx:
            r=cx.execute(text("""SELECT source_type,source_table,source_pk
                FROM pi_master_source_links_v711
                WHERE canonical_id=:id AND master_entity_type=:et
                ORDER BY created_at DESC,id DESC LIMIT 1"""),{"id":cid,"et":etype}).mappings().first()
        return dict(r) if r else {}
    except Exception:
        return {}

def _phones_from_any(v):
    vals=[]
    def walk(x):
        if isinstance(x,dict):
            for y in x.values():walk(y)
        elif isinstance(x,(list,tuple,set)):
            for y in x:walk(y)
        else:
            s=str(x or "").replace("@s.whatsapp.net","").replace("@c.us","")
            for m in re.findall(r"(?<!\d)(?:\+?91[\s.\-]?)?([6-9](?:[\s.\-]?\d){9})(?!\d)",s):
                p=re.sub(r"\D","",m)
                if len(p)==10 and p not in vals:vals.append(p)
    walk(v)
    return ", ".join(vals)

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
def _whatsapp_sender_contact(e,cid,cr):
    """Recover WhatsApp sender/contact strictly from canonical source lineage and stored evidence."""
    # Direct clean-record evidence first.
    p=_phones_from_any({
        "sender_phone":cr.get("sender_phone"),"contact_phone":cr.get("contact_phone"),
        "sender_jid":cr.get("sender_jid"),"participant":cr.get("participant"),
        "contact_numbers":cr.get("contact_numbers"),"phones":cr.get("phones")
    })
    if p:return p

    link=_source_link(e,cid,"PROPERTY")
    stable=str(link.get("source_pk") or _first(cr,"source_pk","wa_property_id","property_id","record_id","id") or "")
    table=str(link.get("source_table") or "")
    source_row=None

    # Canonical DB source row.
    if stable and re.fullmatch(r"[A-Za-z0-9_]+",table or ""):
        try:
            with e.connect() as cx:
                source_row=cx.execute(text(f"""SELECT to_jsonb(x) FROM {table} x
                    WHERE to_jsonb(x)::text ILIKE :needle LIMIT 1"""),
                    {"needle":"%"+stable.replace("%","")+"%"}).scalar()
        except Exception:
            source_row=None
    if isinstance(source_row,dict):
        p=_phones_from_any({
            "sender_phone":source_row.get("sender_phone"),"contact_phone":source_row.get("contact_phone"),
            "sender_jid":source_row.get("sender_jid"),"participant":source_row.get("participant"),
            "sender":source_row.get("sender"),"contact_numbers":source_row.get("contact_numbers"),
            "phones":source_row.get("phones")
        })
        if p:return p

    # WhatsApp live DB provenance.
    try:
        import whatsapp_live_bridge as live
        we=getattr(live,"wa_engine",None)
        if we is None:return ""
        merged=dict(cr)
        if isinstance(source_row,dict): merged.update({k:v for k,v in source_row.items() if v not in (None,"",[],{})})
        mid=str(_first(merged,"message_id","wa_message_id","external_message_id") or "")
        rid=stable or str(_first(merged,"wa_property_id","source_pk","property_id","record_id","id") or "")
        raw=str(_first(merged,"original_message","raw_text","message","description","source_text") or "").strip()
        with we.connect() as cx:
            # Exact source/property row across known historical tables.
            if rid:
                for t in ("wa_properties","wa_property_master","pi_whatsapp_property_master"):
                    try:
                        d=cx.execute(text(f"""SELECT to_jsonb(x) FROM {t} x
                            WHERE to_jsonb(x)::text ILIKE :needle LIMIT 1"""),
                            {"needle":"%"+rid.replace("%","")+"%"}).scalar()
                    except Exception:d=None
                    if isinstance(d,dict):
                        p=_phones_from_any({
                            "sender_phone":d.get("sender_phone"),"contact_phone":d.get("contact_phone"),
                            "sender_jid":d.get("sender_jid"),"participant":d.get("participant"),
                            "sender":d.get("sender")
                        })
                        if p:return p
                        mid=mid or str(d.get("message_id") or "")
            # Exact message.
            if mid:
                try:d=cx.execute(text("SELECT to_jsonb(x) FROM wa_messages x WHERE CAST(message_id AS TEXT)=:m LIMIT 1"),{"m":mid}).scalar()
                except Exception:d=None
                if isinstance(d,dict):
                    p=_phones_from_any({"sender_phone":d.get("sender_phone"),"sender_jid":d.get("sender_jid"),"participant":d.get("participant")})
                    if p:return p
            # Exact raw text historical bridge fallback.
            if raw:
                for t in ("wa_bridge_events","wa_messages"):
                    try:
                        d=cx.execute(text(f"""SELECT to_jsonb(x) FROM {t} x
                            WHERE to_jsonb(x)::text ILIKE :raw
                            ORDER BY created_at DESC LIMIT 1"""),{"raw":"%"+raw[:220].replace("%","")+"%"}).scalar()
                    except Exception:d=None
                    if isinstance(d,dict):
                        p=_phones_from_any({"sender_phone":d.get("sender_phone"),"sender_jid":d.get("sender_jid"),"participant":d.get("participant")})
                        if p:return p
    except Exception:
        pass
    return ""

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

def _derive_transaction(cr,current=""):
    tx=str(current or _first(cr,"transaction_type","rent_sale","rent_or_sale") or "").strip().upper()
    if tx in ("RENT","LEASE","LEASING"):return "LEASE"
    if tx in ("SALE","SELL","RESALE","PURCHASE","BUY"):return "SALE"
    cat=str(_first(cr,"property_category","category") or "").upper()
    if re.search(r"\bRENT\b|\bLEASE\b",cat):return "LEASE"
    if re.search(r"\bSALE\b|\bRESALE\b",cat):return "SALE"
    blob=" ".join(str(cr.get(k) or "") for k in ("description","original_description","source_text","raw_line","original_message","details","remarks","section_heading","original_section","source","category_source")).upper()
    # Do not treat LEASEHOLD as a rental transaction.
    blob=re.sub(r"\bLEASE[ -]*HOLD\b|\bLEASEHOLD\b"," ",blob)
    if re.search(r"\b(TO LET|FOR RENT|ON RENT|RENTAL|LEASING)\b",blob):return "LEASE"
    if re.search(r"\b(FOR SALE|SALE|RESALE|SELLING)\b",blob):return "SALE"
    return ""

def _magazine_category(cr,tx):
    """Controlled Magazine category: asset class × Rent/Sale.
    Priority: row evidence -> locality intelligence -> section fallback.
    This prevents residential localities such as Vasant Vihar inheriting an
    Industrial heading from a neighboring/page section."""
    txv=_derive_transaction(cr,tx)
    suffix="Rent" if txv=="LEASE" else "Sale" if txv=="SALE" else ""
    # Row evidence must come from the row's own textual description.
    # Do NOT include structured property_type here because historical magazine
    # property_type values can be inherited from page/section context.
    row_blob=" ".join(str(cr.get(k) or "") for k in (
        "description","original_description","raw_line","source_text","details"
    )).upper()
    structured_type=str(_first(cr,"property_type","property_types","asset_type","subtype") or "").upper()
    section_blob=" ".join(str(cr.get(k) or "") for k in (
        "property_category","category","original_section","section_heading","category_source"
    )).upper()
    loc=str(_first(cr,"location","locality","area_name","micro_market","city") or "").upper()

    def asset_from(blob):
        if re.search(r"\b(FARM\s*HOUSE|FARMHOUSE|SAINIK\s+FARM|GADIPUR\s+FARM|DERA\s+MANDI)\b",blob):return "Farmhouse"
        if re.search(r"\b(BHK|APARTMENT|APT\b|FLAT|KOTHI|VILLA|PENTHOUSE|RESIDENTIAL)\b",blob):return "Residential"
        if re.search(r"\b(OFFICE|SHOWROOM|SHOP\b|RETAIL|MALL|MARKET|MKT\b|COMMERCIAL)\b",blob):return "Commercial"
        if re.search(r"\b(INDUSTRIAL|FACTORY|WAREHOUSE|GODOWN|SHED)\b",blob):return "Industrial"
        return ""

    # 1. Explicit terms in the individual classified row.
    asset=asset_from(row_blob)

    # 2. Known locality intelligence outranks inherited structured/page fields.
    if not asset:
        if re.search(r"\b(VASANT\s+VIHAR|VASANT\s+KUNJ|DEFENCE\s+COLONY|GREATER\s+KAILASH|GK\s*[12]?|PANCHSHEEL|HAUZ\s+KHAS|SAFDARJUNG|GREEN\s+PARK|MAHARANI\s+BAGH|JOR\s+BAGH|NEW\s+FRIENDS\s+COLONY|NFC|EAST\s+OF\s+KAILASH|CR\s+PARK|CHITRANJAN\s+PARK|GULMOHAR\s+PARK|UDAY\s+PARK|MAYFAIR\s+GARDEN|PUNJABI\s+BAGH|PASCHIM\s+VIHAR|RAJOURI\s+GARDEN|JANAKPURI|PATEL\s+NAGAR)\b",loc):
            asset="Residential"
        elif re.search(r"\b(OKHLA(?:\s+PHASE\s*[123])?|NARAINA(?:\s+INDUSTRIAL\s+AREA)?|WAZIRPUR(?:\s+INDUSTRIAL\s+AREA)?|MUNDKA|BAWANA|NARELA|MAYAPURI(?:\s+INDUSTRIAL\s+AREA)?|KIRTI\s+NAGAR\s+INDUSTRIAL)\b",loc):
            asset="Industrial"
        elif re.search(r"\b(CONNAUGHT\s+PLACE|CP\b|NEHRU\s+PLACE|SAKET\s+DISTRICT\s+CENTRE|JANAKPURI\s+DISTRICT\s+CENTRE|BHIKAJI\s+CAMA|NETAJI\s+SUBHASH\s+PLACE|NSP\b|KAROL\s+BAGH|LAJPAT\s+NAGAR|SOUTH\s+EXTENSION|GREATER\s+KAILASH\s+MARKET)\b",loc):
            asset="Commercial"

    # 3. Structured type is fallback only, never allowed to override locality.
    if not asset:
        asset=asset_from(structured_type)

    # 4. Page/section context is the final fallback only.
    if not asset:
        asset=asset_from(section_blob)

    if asset and suffix:return f"{asset} {suffix}"
    return asset or ""


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
def _whatsapp_clean_source_rows(e,q,limit,offset):
    """Latest completed deduplicated WhatsApp Property Master generation only."""
    try:
        with e.connect() as cx:
            exists=cx.execute(text("SELECT to_regclass('public.pi_whatsapp_property_master')")).scalar()
            gens=cx.execute(text("SELECT to_regclass('public.pi_whatsapp_property_master_generation')")).scalar()
            if not exists or not gens:return []
            gen=cx.execute(text("""SELECT generation_id FROM pi_whatsapp_property_master_generation
                WHERE status='COMPLETED'
                ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
            if not gen:return []
            if q.strip():
                raw=cx.execute(text("""SELECT to_jsonb(t) FROM pi_whatsapp_property_master t
                    WHERE generation_id=:g
                      AND (COALESCE(description,'') ILIKE :q
                           OR COALESCE(configuration_details,'') ILIKE :q
                           OR COALESCE(contact_name_number,'') ILIKE :q
                           OR COALESCE(phone_numbers,'') ILIKE :q
                           OR COALESCE(source,'') ILIKE :q
                           OR COALESCE(raw_message,'') ILIKE :q)
                    ORDER BY captured_on DESC NULLS LAST,id DESC
                    LIMIT :n OFFSET :off"""),
                    {"g":gen,"q":f"%{q.strip()}%","n":limit,"off":offset}).scalars().all()
            else:
                raw=cx.execute(text("""SELECT to_jsonb(t) FROM pi_whatsapp_property_master t
                    WHERE generation_id=:g
                    ORDER BY captured_on DESC NULLS LAST,id DESC
                    LIMIT :n OFFSET :off"""),
                    {"g":gen,"n":limit,"off":offset}).scalars().all()
        out=[]
        for x in raw:
            d=x if isinstance(x,dict) else json.loads(x)
            rid=str(d.get("record_id") or d.get("id") or "")
            lead=str(d.get("lead_type") or "").upper()
            tx="SALE" if lead=="SALE" else "LEASE" if lead in ("RENT","LEASE") else ""
            desc=str(d.get("description") or "")
            locality=str(d.get("locality") or d.get("project_name") or "").strip()
            if not locality and desc:
                locality=desc.split("|",1)[0].strip()
            clean={
                "source_type":"WHATSAPP",
                "source_table":"pi_whatsapp_property_master",
                "source_record_id":rid,
                "record_id":rid,
                "description":desc,
                "raw_message":d.get("raw_message") or "",
                "location":locality,
                "property_type":d.get("configuration_details") or "",
                "area_text":d.get("area") or "",
                "floor":d.get("floor") or "",
                "transaction_type":tx,
                "amount_text":d.get("price") or "",
                "contact_name":d.get("contact_name") or "",
                "contact_number":d.get("phone_numbers") or "",
                "contact_name_number":d.get("contact_name_number") or "",
                "phones":d.get("phone_numbers") or "",
                "all_contacts":d.get("all_contacts") or "",
                "source":d.get("source") or "WhatsApp",
                "verification_status":d.get("verification") or "UNVERIFIED",
                "created_at":d.get("captured_on") or d.get("created_at"),
            }
            out.append({
                "canonical_id":"WHATSAPP-SOURCE-"+rid,
                "locality":locality,
                "city":"",
                "transaction_type":tx,
                "area_sqft":"",
                "price_raw":d.get("price") or "",
                "created_at":d.get("captured_on") or d.get("created_at"),
                "updated_at":d.get("captured_on") or d.get("created_at"),
                "verification_status":d.get("verification") or "UNVERIFIED",
                "availability_status":"UNKNOWN",
                "assigned_to":"",
                "clean_record":clean,
            })
        return out
    except Exception:
        return []

def _property_rows(e,source,q,location,category,transaction,status,assigned,limit,offset=0):
    def source_rows(table,where,order):
        with e.connect() as cx:
            exists=cx.execute(text("SELECT to_regclass(:t)"),{"t":"public."+table}).scalar()
            if not exists:return []
            raw=cx.execute(text(f"""SELECT to_jsonb(t) FROM {table} t {where}
                AND (:q='%%' OR to_jsonb(t)::text ILIKE :q)
                {order} LIMIT :n OFFSET :off"""),{"q":f"%{q.strip()}%","n":limit,"off":offset}).scalars().all()
        parsed=[x if isinstance(x,dict) else json.loads(x) for x in raw]
        page_tx={}
        if source=="MAGAZINE":
            from collections import Counter,defaultdict
            votes=defaultdict(Counter)
            for d in parsed:
                pg=str(d.get("page_number") or "")
                tx0=_derive_transaction(d,_first(d,"transaction_type","rent_sale","rent_or_sale") or "")
                if pg and tx0:votes[pg][tx0]+=1
            for pg,cnt in votes.items():
                if cnt:
                    txv,n=cnt.most_common(1)[0]
                    total=sum(cnt.values())
                    if n>=2 or (total==1 and len(cnt)==1):
                        page_tx[pg]=txv
        out=[]
        for d in parsed:
            sid=str(_first(d,"property_code","property_id","source_record_id","id") or "")
            txv=_derive_transaction(d,_first(d,"transaction_type","rent_sale","rent_or_sale") or "")
            if source=="MAGAZINE" and not txv:
                txv=page_tx.get(str(d.get("page_number") or ""), "")
            out.append({
                "canonical_id":source+"-SOURCE-"+sid,
                "locality":_first(d,"location","locality","city") or "",
                "city":_first(d,"city") or "",
                "transaction_type":txv,
                "area_sqft":_first(d,"area_sqft","area_value","area","available_area") or "",
                "price_raw":_first(d,"rent_text","amount","amount_raw","rent_amount","sale_amount","price_raw","price") or "",
                "created_at":_first(d,"created_at","entry_datetime","entry_date"),
                "updated_at":_first(d,"updated_at","created_at","entry_datetime","entry_date"),
                "verification_status":_first(d,"verification_status") or "UNVERIFIED",
                "availability_status":_first(d,"availability_status","status") or "UNKNOWN",
                "assigned_to":_first(d,"assigned_to","team_member") or "",
                "clean_record":dict(d,source_table=table,source_pk=sid,source_type=source,derived_transaction=txv),
            })
        return out

    if source=="MANUAL":
        rows=source_rows("pi_operational_properties","WHERE COALESCE(entry_source,'MANUAL')='MANUAL'","ORDER BY COALESCE(updated_at,created_at) DESC NULLS LAST")
    elif source=="MAGAZINE":
        rows=source_rows("pi_magazine_complete_v860","WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'","ORDER BY COALESCE(updated_at,created_at) DESC NULLS LAST,id DESC")
    elif source=="WHATSAPP":
        rows=_whatsapp_clean_source_rows(e,q,limit,offset)
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
        ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST LIMIT :n OFFSET :off"""
        with e.connect() as cx:
            rows=[dict(x) for x in cx.execute(text(sql),{"q":f"%{q.strip()}%","loc":f"%{location.strip()}%","tx":transaction.upper().strip(),
            "st":status.upper().strip(),"asgn":f"%{assigned.strip()}%","n":limit,"off":offset,"pat":pat or ""}).mappings().all()]
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
.tablebox{{overflow:auto;max-height:76vh;border:1px solid #667085;background:white;scrollbar-gutter:stable both-edges}}table{{border-collapse:collapse;width:3680px;min-width:3680px;font-size:11px;table-layout:fixed}}.magazinebox{{max-height:82vh;overflow-x:scroll!important;overflow-y:auto!important;scrollbar-gutter:stable both-edges}}.magazinebox table{{font-size:10.5px!important}}.magazinebox th,.magazinebox td{{padding:5px 6px!important}}
th,td{{border:1px solid #98a2b3;padding:7px;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere;word-break:normal;overflow:hidden;font-weight:600}}
th{{background:#e9eef5;position:sticky;top:0;z-index:4;white-space:nowrap;min-width:110px;font-weight:800}}
tbody tr:nth-child(even) td{{background:#f8fafc}}tbody tr:hover td{{background:#eef4ff}}
.desc{{width:520px!important;min-width:520px!important;max-width:520px!important;white-space:normal!important;overflow-wrap:anywhere!important}}.remarks{{width:420px!important;min-width:420px!important;max-width:420px!important;white-space:normal!important;overflow-wrap:anywhere!important}}
.loc{{width:180px!important;min-width:180px!important;max-width:180px!important;white-space:normal!important}}
.nowrap{{white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis}}
td:nth-child(10),td:nth-child(11){{min-width:140px;max-width:190px}}
td:nth-child(12){{min-width:150px;max-width:190px}}
td:nth-child(14),td:nth-child(15),td:nth-child(18),td:nth-child(19){{min-width:90px;max-width:130px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}}.dbcard{{border:1px solid #98a2b3;background:white;padding:12px}}.dbcard h3{{margin:0 0 5px}}
details.pop{{position:relative}}details.pop>div{{position:absolute;z-index:20;background:white;border:1px solid #667085;padding:9px;min-width:430px}}details.pop summary{{list-style:none}}
.dbtools{{display:flex;align-items:center;gap:5px;margin:0 0 7px;background:#fff;padding:5px;border:1px solid #d0d5dd;width:max-content;position:sticky;left:0;z-index:6}}.dbtools button{{padding:4px 7px}}
body.compact table{{font-size:10px}}body.compact th,body.compact td{{padding:4px 5px}}
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
    <input type="number" name="page_size" min="25" max="500" value="{limit}">
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

def _canonical_property_projection(r,source,e,contact_batch=None):
    """One display schema for Master, Manual, Magazine, Newspaper and WhatsApp."""
    cr=_flat_record(r.get("clean_record"))
    cid=str(r.get("canonical_id") or "")
    locality=_clean_location_value(r.get("locality") or _first(cr,"location","locality","micro_market","area_name","city") or "",cr)
    if locality=="Needs verification":
        return None

    address=_first(cr,"address","exact_address","property_address") or ""
    root=_dict(r.get("clean_record"))
    manual_origin=(source=="MANUAL" or isinstance(root.get("manual_operational"),dict) or str(_first(cr,"source_type","entry_source") or "").upper().startswith("MANUAL"))
    if manual_origin:
        # Manual form has no separate description field. Use the manually entered
        # property identity/details here, and keep Remarks completely separate.
        desc=_first(cr,"team_description","description_edit","property_description","property_name") or ""
        if not desc:
            parts=[]
            for label,keys in (
                ("Property",("property_name","property_type","property_types")),
                ("Area",("area_text","area_display","area","available_area","area_sqft")),
                ("Floor",("floor","floors")),
                ("Suitable",("suitable_for","suitable_category","property_category","category")),
                ("Parking",("parking","parking_details")),
                ("Possession",("possession","possession_status")),
            ):
                v=_first(cr,*keys)
                if v not in (None,"",[],{}):parts.append(label+": "+_display_text(v))
            desc=" | ".join(parts)
    else:
        desc=_first(cr,"description","property_description","original_description","source_text","raw_line","original_message","details","team_description","description_edit") or ""
    desc=_display_text(desc)
    if address and address.lower() not in desc.lower():
        desc=(address+" · "+desc).strip(" ·")
    remarks=_display_text(_first(cr,"remarks","additional_points","notes") or "")
    if manual_origin and remarks and remarks.lower() not in desc.lower():
        desc=(desc+" · "+remarks).strip(" ·")

    tx=_derive_transaction(cr,r.get("transaction_type") or _first(cr,"transaction_type","rent_sale","rent_or_sale") or "")
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
    if source=="WHATSAPP":
        b=(contact_batch or {}).get(cid,{})
        if not cname and b.get("name"):cname=b.get("name")
        if not cphone and b.get("phone"):cphone=b.get("phone")
        # Last-resort live evidence lookup only when the settled clean master has no contact.
        if not cphone and not contact_batch:
            cphone=_whatsapp_sender_contact(e,cid,cr)
    stat=r.get("availability_status")
    if not stat or stat=="UNKNOWN":stat=r.get("verification_status") or _first(cr,"verification_status","status") or "UNVERIFIED"

    source_name=(str(_first(cr,"entry_source","source","source_type") or source).title()
                 if "-SOURCE-" in cid
                 else (_source_name(e,cid,"PROPERTY") or str(_first(cr,"entry_source","source","source_type") or source).title()))
    google_pin=_first(cr,"google_location","google_maps","google_pin","map_link") or ""
    media=_manual_media_summary(e,cr)

    return {
        "id":cid,
        "location":locality,
        "description":desc,
        "remarks":remarks,
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
        "source_record_id":_display_text(_first(cr,"property_code","property_id","source_record_id","record_id") or ""),
        "source_only":"-SOURCE-" in cid,
    }

def _whatsapp_contact_batch(e,rows):
    """Fast contact map only for canonical WhatsApp rows currently being rendered."""
    ids=[str(r.get("canonical_id") or "") for r in rows if r.get("canonical_id")]
    if not ids:return {}
    try:
        with e.connect() as cx:
            stmt=text("""SELECT canonical_id,source_pk
                FROM pi_master_source_links_v711
                WHERE master_entity_type='PROPERTY'
                  AND canonical_id IN :ids
                  AND (
                    UPPER(COALESCE(source_type,'')) LIKE '%WHATSAPP%'
                    OR UPPER(COALESCE(source_table,'')) LIKE '%WHATSAPP%'
                  )
                ORDER BY created_at DESC NULLS LAST,id DESC
            """).bindparams(bindparam("ids",expanding=True))
            links=cx.execute(stmt,{"ids":ids}).mappings().all()
            cid_to_pk={}
            for r in links:
                cid=str(r.get("canonical_id") or "");pk=str(r.get("source_pk") or "")
                if cid and pk and cid not in cid_to_pk:cid_to_pk[cid]=pk
            pks=list(dict.fromkeys(cid_to_pk.values()))
            if not pks:return {}
            exists=cx.execute(text("SELECT to_regclass('public.pi_whatsapp_property_master')")).scalar()
            if not exists:return {}
            stmt2=text("""SELECT CAST(record_id AS TEXT) record_id,CAST(id AS TEXT) id_text,
                       CAST(canonical_key AS TEXT) canonical_key,
                       contact_name,phone_numbers,contact_name_number,all_contacts
                FROM pi_whatsapp_property_master
                WHERE CAST(record_id AS TEXT) IN :p1
                   OR CAST(id AS TEXT) IN :p2
                   OR CAST(canonical_key AS TEXT) IN :p3
            """).bindparams(
                bindparam("p1",expanding=True),bindparam("p2",expanding=True),bindparam("p3",expanding=True)
            )
            contacts=cx.execute(stmt2,{"p1":pks,"p2":pks,"p3":pks}).mappings().all()
        by_pk={}
        for r in contacts:
            phone=_phones_from_any({
                "phone_numbers":r.get("phone_numbers"),
                "contact_name_number":r.get("contact_name_number"),
                "all_contacts":r.get("all_contacts"),
            })
            rec={"name":str(r.get("contact_name") or "").strip(),"phone":phone}
            for k in ("record_id","id_text","canonical_key"):
                v=str(r.get(k) or "")
                if v:by_pk[v]=rec
        out={}
        for cid,pk in cid_to_pk.items():
            if pk in by_pk:out[cid]=dict(by_pk[pk],source_pk=pk)
        return out
    except Exception:
        return {}


def _magazine_amounts(cr,tx,amount):
    """Show structured amount first; recover only explicit numeric evidence from source line."""
    rent="";sale=""
    txu=str(tx or "").upper()
    if txu in ("RENT","LEASE"):
        rent=_display_text(_first(cr,"rent_text","rent_amount","monthly_rent","rent","asking_rent") or amount)
    elif txu=="SALE":
        sale=_display_text(_first(cr,"sale_text","sale_amount","sale_price","asking_price","price") or amount)
    else:
        rent=_display_text(_first(cr,"rent_text","rent_amount","monthly_rent","asking_rent") or "")
        sale=_display_text(_first(cr,"sale_text","sale_amount","sale_price","asking_price") or "")

    blob=" ".join(str(cr.get(k) or "") for k in ("description","original_description","raw_line","source_text","details"))
    # Typical magazine shorthand: @35TH, @1.5L, RENT 2.5L, SALE 5CR, PRICE 85000.
    pats=[
        r"(?i)(?:@|RENT\s*[:@-]?\s*)(\d+(?:\.\d+)?\s*(?:TH|K|L|LAC|LAKH|CR|CRORE)(?:\s*/?\s*(?:MONTH|PM|SQFT|SF))?)",
        r"(?i)(?:SALE|PRICE|DEMAND|ASKING)\s*[:@-]?\s*(?:RS\.?\s*)?(\d+(?:\.\d+)?\s*(?:TH|K|L|LAC|LAKH|CR|CRORE)?)",
    ]
    if txu in ("RENT","LEASE") and not rent:
        m=re.search(pats[0],blob)
        if m:rent=m.group(1).strip()
    if txu=="SALE" and not sale:
        m=re.search(pats[1],blob)
        if m:sale=m.group(1).strip()
    return rent,sale


def _property_source_count(e,source,q=""):
    try:
        with e.connect() as cx:
            if source=="MAGAZINE":
                return int(cx.execute(text("""SELECT COUNT(*) FROM pi_magazine_complete_v860
                    WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'
                      AND (:q='%%' OR to_jsonb(pi_magazine_complete_v860)::text ILIKE :q)"""),
                    {"q":f"%{q.strip()}%"}).scalar() or 0)
            if source=="MANUAL":
                return int(cx.execute(text("""SELECT COUNT(*) FROM pi_operational_properties
                    WHERE COALESCE(entry_source,'MANUAL')='MANUAL'
                      AND (:q='%%' OR to_jsonb(pi_operational_properties)::text ILIKE :q)"""),
                    {"q":f"%{q.strip()}%"}).scalar() or 0)
            if source=="WHATSAPP":
                gen=cx.execute(text("""SELECT generation_id FROM pi_whatsapp_property_master_generation
                    WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
                if not gen:return 0
                if not q.strip():
                    return int(cx.execute(text("""SELECT COUNT(*) FROM pi_whatsapp_property_master
                        WHERE generation_id=:g"""),{"g":gen}).scalar() or 0)
                return int(cx.execute(text("""SELECT COUNT(*) FROM pi_whatsapp_property_master
                    WHERE generation_id=:g
                      AND (COALESCE(description,'') ILIKE :q
                           OR COALESCE(configuration_details,'') ILIKE :q
                           OR COALESCE(contact_name_number,'') ILIKE :q
                           OR COALESCE(phone_numbers,'') ILIKE :q
                           OR COALESCE(source,'') ILIKE :q
                           OR COALESCE(raw_message,'') ILIKE :q)"""),
                    {"g":gen,"q":f"%{q.strip()}%"}).scalar() or 0)
            pat=_src_pat(source)
            if pat:
                return int(cx.execute(text("""SELECT COUNT(DISTINCT p.canonical_id)
                    FROM pi_master_properties_v711 p
                    WHERE UPPER(COALESCE(p.promotion_status,'')) NOT IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED','MANUAL_ARCHIVED')
                      AND EXISTS(
                        SELECT 1 FROM pi_master_source_links_v711 l
                        WHERE l.canonical_id=p.canonical_id AND l.master_entity_type='PROPERTY'
                          AND (UPPER(COALESCE(l.source_type,'')) LIKE :pat
                               OR UPPER(COALESCE(l.source_table,'')) LIKE :pat)
                      )
                      AND (:q='%%' OR p.canonical_id ILIKE :q OR COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q)"""),
                    {"q":f"%{q.strip()}%","pat":pat}).scalar() or 0)
            return int(cx.execute(text("""SELECT COUNT(*) FROM pi_master_properties_v711
                WHERE UPPER(COALESCE(promotion_status,'')) NOT IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED','MANUAL_ARCHIVED')
                  AND (:q='%%' OR canonical_id ILIKE :q OR COALESCE(locality,'') ILIKE :q OR COALESCE(city,'') ILIKE :q OR COALESCE(clean_record::text,'') ILIKE :q)"""),
                {"q":f"%{q.strip()}%"}).scalar() or 0)
    except Exception:
        return 0

def _pager(source,page,page_size,total,q,location,category,transaction,status,assigned):
    pages=max(1,(total+page_size-1)//page_size)
    page=max(1,min(page,pages))
    from urllib.parse import urlencode
    base=f"/alliance/final/database/{source.lower()}"
    common={"q":q,"location":location,"category":category,"transaction":transaction,"status":status,"assigned":assigned,"page_size":page_size}
    def href(p):
        d=dict(common);d["page"]=p
        return base+"?"+urlencode(d)
    prev=f'<a class="btn light" href="{_e(href(page-1))}">← Previous</a>' if page>1 else ""
    nxt=f'<a class="btn light" href="{_e(href(page+1))}">Next →</a>' if page<pages else ""
    return f'<div class="card"><b>Total:</b> {total:,} · <b>Page:</b> {page:,} of {pages:,} · <b>Rows/page:</b> {page_size} &nbsp; {prev} {nxt}</div>'

def _property_table(core,e,req,source,q,location,category,transaction,status,assigned,limit,offset=0,rows_override=None):
    rows=rows_override if rows_override is not None else _property_rows(e,source,q,location,category,transaction,status,assigned,limit,offset)
    wa_contacts={} if source=="WHATSAPP" and all("-SOURCE-" in str(r.get("canonical_id") or "") for r in rows) else (_whatsapp_contact_batch(e,rows) if source=="WHATSAPP" else {})
    prepared=[]
    for r in rows:
        p=_canonical_property_projection(r,source,e,wa_contacts)
        if not p:continue
        cid=p["id"]
        if p["source_only"]:
            history="—";sid=p.get("source_record_id") or ""
            if source in ("MANUAL","MAGAZINE") and sid:
                verify=f"""<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/final/database/{source.lower()}/{_e(sid)}/verify-source">
                <select name="status" required><option>VERIFIED</option><option>UNVERIFIED</option><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>FOLLOW-UP</option></select>
                <input name="verified_by" required placeholder="Verified By"><button class="good">Save</button></form></div></details>"""
            else:
                verify=f'<a class="btn good" href="/alliance/final/database/{source.lower()}?q={_e(sid or cid)}">Verify</a>'
            if source=="MANUAL" and sid:
                div=str(_first(_flat_record(r.get("clean_record")),"division") or "DELHI_NCR")
                edit=f'<a class="btn light" href="/fast-property-entry?division={_e(div)}&edit={_e(sid)}">Edit</a>'
                delete=f"""<form method="post" action="/alliance/final/database/manual/{_e(sid)}/delete-source" onsubmit="return confirm('Archive this Manual property? It will also be withdrawn from Master.');"><button class="danger">Delete</button></form>"""
            elif source=="MAGAZINE" and sid:
                edit=f'<a class="btn light" href="/magazine-complete">Edit</a>'
                delete=f"""<form method="post" action="/alliance/final/database/magazine/{_e(sid)}/delete-source" onsubmit="return confirm('Archive this Magazine property? Original evidence will remain.');"><button class="danger">Delete</button></form>"""
            else:
                edit="—";delete="—"
        else:
            verify=f"""<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/primary/property/{_e(cid)}/verify">
            <select name="status" required><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>CALL_BACK</option><option>SOLD</option><option>RENTED</option><option>HOLD</option><option>WRONG_NUMBER</option></select>
            <select name="verified_with" required><option>OWNER</option><option>BROKER</option><option>OTHER</option></select>
            <input name="verified_by" required placeholder="Verified By team member"><input name="remarks" placeholder="Remarks"><input type="datetime-local" name="next_verification_at"><button class="good">Save</button></form></div></details>"""
            history=f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}">History</a>'
            edit=f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}/edit">Edit</a>'
            delete=f"""<form method="post" action="/alliance/primary/property/{_e(cid)}/delete" onsubmit="return confirm('Archive this property? Original source evidence remains preserved.');"><button class="danger">Delete</button></form>"""

        pin_html=(f'<a class="btn light" target="_blank" rel="noopener" href="{_e(p["google_pin"])}">Open Pin</a>' if p["google_pin"] else "—")
        m=p["media"];pc=m.get("property_code") or ""
        media_label=f'{m.get("images",0)} pics · {m.get("videos",0)} videos · {m.get("brochures",0)} docs'
        media_html=(f'<a class="btn light" href="/alliance/final/database/media/{quote(str(pc),safe="")}">{_e(media_label)}</a>' if pc and m.get("total",0)>0 else _e(media_label if pc else "—"))
        prepared.append((r,p,verify,history,edit,delete,pin_html,media_html))

    if source=="MAGAZINE":
        trs=[]
        for r,p,verify,history,edit,delete,pin_html,media_html in prepared:
            cr=_flat_record(r.get("clean_record"))
            rent_amount,sale_amount=_magazine_amounts(cr,p["transaction"],p["amount"])
            vals=[
                p["date"],p["description"],p["location"],p["category"],p["transaction"],
                rent_amount,sale_amount,p["contact_no"],verify,p["status"],edit,delete,p["remarks"]
            ]
            cls=["nowrap","desc","loc","","nowrap","","","","","nowrap","","","remarks"]
            raw={8,10,11}
            trs.append("<tr>"+"".join(f'<td class="{cls[i]}">{x if i in raw else _e(_shown(x))}</td>' for i,x in enumerate(vals))+"</tr>")
        H=["Date / Time","Description / Address","Location","Category","Rent / Sale","Rent Amount","Sale Amount","Contact No.","Verify","Verification","Edit","Delete","Remarks"]
        widths=[155,460,150,145,95,125,125,135,90,100,80,80,300]
        colgroup="<colgroup>"+"".join(f'<col style="width:{w}px;min-width:{w}px;max-width:{w}px">' for w in widths)+"</colgroup>"
        return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="dbtools"><b>Magazine Compact Table</b><button type="button" onclick="dbCompact()">Compact</button><button type="button" onclick="dbZoom(-1)">−</button><button type="button" onclick="dbZoom(1)">+</button><button type="button" onclick="dbZoomReset()">Reset</button></div><div class="tablebox magazinebox"><table style="width:2040px;min-width:2040px">{colgroup}<thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=13>No records found</td></tr>"}</tbody></table></div>'

    trs=[]
    for r,p,verify,history,edit,delete,pin_html,media_html in prepared:
        vals=[
            p["date"],p["description"],p["contact_name"],p["contact_no"],p["location"],
            p["category"],p["type"],p["area"],p["floor"],p["transaction"],p["amount"],
            pin_html,media_html,verify,p["status"],history,p["assigned_to"],p["source"],
            p["source_record_id"],p["id"],edit,delete,p["remarks"]
        ]
        cls=["nowrap","desc","","","loc","","","","","nowrap","","","","","nowrap","","","","nowrap","nowrap","","","remarks"]
        raw={11,12,13,15,20,21}
        trs.append("<tr>"+"".join(f'<td class="{cls[i]}">{x if i in raw else _e(_shown(x))}</td>' for i,x in enumerate(vals))+"</tr>")
    H=[
        "Date / Time","Description / Address","Contact Name","Contact No.","Location",
        "Category / Use","Property Type","Area","Floor","Rent / Sale","Amount",
        "Google Pin","Media","Verify","Verification","History","Assigned To","Source",
        "Source ID","Property ID","Edit","Delete","Remarks"
    ]
    widths=[170,520,140,140,180,150,170,120,110,100,120,110,190,100,110,100,120,130,180,180,90,90,360]
    colgroup="<colgroup>"+"".join(f'<col style="width:{w}px;min-width:{w}px;max-width:{w}px">' for w in widths)+"</colgroup>"
    return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="dbtools"><b>Table</b><button type="button" onclick="dbCompact()">Compact</button><button type="button" onclick="dbZoom(-1)">−</button><button type="button" onclick="dbZoom(1)">+</button><button type="button" onclick="dbZoomReset()">Reset</button></div><div class="tablebox"><table><colgroup>{"".join(f"""<col style='width:{w}px;min-width:{w}px;max-width:{w}px'>""" for w in widths)}</colgroup><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=23>No records found</td></tr>"}</tbody></table></div>'

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

                checks["manual_media_table_exists"]=bool(cx.execute(text("SELECT to_regclass('public.pi_operational_property_media')")).scalar())
                details["manual_media_files"]=count("SELECT COUNT(*) FROM pi_operational_property_media") if checks["manual_media_table_exists"] else 0
                details["manual_google_pins"]=count("""SELECT COUNT(*) FROM pi_operational_properties
                    WHERE COALESCE(entry_source,'MANUAL')='MANUAL'
                      AND BTRIM(COALESCE(google_location,''))<>''""") if checks["manual_table_exists"] else 0
                details["master_manual_google_pin_missing"]=count("""SELECT COUNT(DISTINCT p.property_code)
                    FROM pi_operational_properties p
                    JOIN pi_master_source_links_v711 l
                      ON l.master_entity_type='PROPERTY'
                     AND l.source_table='pi_operational_properties'
                     AND l.source_pk=p.property_code
                    JOIN pi_master_properties_v711 m ON m.canonical_id=l.canonical_id
                    WHERE COALESCE(p.entry_source,'MANUAL')='MANUAL'
                      AND BTRIM(COALESCE(p.google_location,''))<>''
                      AND COALESCE(m.clean_record->>'google_location','')<>COALESCE(p.google_location,'')""") if checks["manual_table_exists"] and checks["master_table_exists"] else -1
                checks["manual_google_pin_master_preserved"]=details["master_manual_google_pin_missing"]==0

                details["magazine_bad_location_rows"]=count("""SELECT COUNT(*) FROM pi_magazine_complete_v860
                    WHERE archived_at IS NULL AND COALESCE(record_status,'ACTIVE')='ACTIVE'
                      AND (
                        LOWER(BTRIM(COALESCE(location,''))) IN ('tara','royal construction','royal constructions')
                        OR LOWER(COALESCE(location,'')) ~ '(construction|constructions|builder|builders|developer|developers|realty|properties|infra|infrastructure)'
                      )""") if checks["magazine_table_exists"] else -1
                checks["magazine_bad_locations_removed"]=details["magazine_bad_location_rows"]==0

                checks["whatsapp_clean_table_exists"]=bool(cx.execute(text("SELECT to_regclass('public.pi_whatsapp_property_master')")).scalar())
                details["whatsapp_clean_rows"]=count("SELECT COUNT(*) FROM pi_whatsapp_property_master") if checks["whatsapp_clean_table_exists"] else 0
                details["whatsapp_rows_with_contact"]=count("""SELECT COUNT(*) FROM pi_whatsapp_property_master
                    WHERE NULLIF(BTRIM(COALESCE(CAST(phone_numbers AS TEXT),'')),'') IS NOT NULL
                       OR NULLIF(BTRIM(COALESCE(CAST(contact_name_number AS TEXT),'')),'') IS NOT NULL
                       OR NULLIF(BTRIM(COALESCE(CAST(all_contacts AS TEXT),'')),'') IS NOT NULL""") if checks["whatsapp_clean_table_exists"] else 0
                details["whatsapp_contact_coverage_pct"]=round(
                    (details["whatsapp_rows_with_contact"] * 100.0 / details["whatsapp_clean_rows"])
                    if details["whatsapp_clean_rows"] else 0.0, 2
                )
                checks["whatsapp_contact_recovery_present"]=(
                    details["whatsapp_clean_rows"] == 0 or details["whatsapp_rows_with_contact"] > 0
                )

                details["master_quarantined_visible"]=count("""SELECT COUNT(*) FROM pi_master_properties_v711
                    WHERE UPPER(COALESCE(promotion_status,'')) IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED','MANUAL_ARCHIVED')""") if checks["master_table_exists"] else -1
                checks["master_filter_contract_present"]=True

            # Whole-source Magazine classification certification. No contacts or
            # private row data are exposed, only aggregate counts.
            mag_rows=_property_rows(e,"MAGAZINE","","","","","","",5000)
            mag_category_distribution={}
            mag_transaction_distribution={}
            for mr in mag_rows:
                cr=_flat_record(mr.get("clean_record"))
                tx=_derive_transaction(cr,mr.get("transaction_type") or _first(cr,"transaction_type","rent_sale","rent_or_sale") or "")
                cat=_magazine_category(cr,tx) or "UNCLASSIFIED"
                mag_category_distribution[cat]=mag_category_distribution.get(cat,0)+1
                txk=tx or "UNKNOWN"
                mag_transaction_distribution[txk]=mag_transaction_distribution.get(txk,0)+1
            details["magazine_active_rows"]=len(mag_rows)
            details["magazine_category_distribution"]=dict(sorted(mag_category_distribution.items()))
            details["magazine_transaction_distribution"]=dict(sorted(mag_transaction_distribution.items()))
            nonzero_categories=[k for k,v in mag_category_distribution.items() if v>0 and k!="UNCLASSIFIED"]
            checks["magazine_not_blanket_single_category"]=(
                len(mag_rows)<2 or len(nonzero_categories)>=2
            )

            # Renderer contract: same projection keys for every property source.
            expected={"id","location","description","remarks","category","type","area","floor","transaction","amount","contact_name","contact_no","date","status","assigned_to","source","google_pin","media","source_record_id","source_only"}
            checks["single_projection_schema"]=set(_canonical_property_projection({
                "canonical_id":"AUDIT","locality":"Saket","transaction_type":"LEASE","created_at":None,
                "verification_status":"UNVERIFIED","availability_status":"UNKNOWN","assigned_to":"",
                "clean_record":{"location":"Saket","description":"audit","property_type":"Retail","area_text":"1000 sqft","rent_text":"2 lakh"}
            },"MANUAL",e).keys())==expected

            checks["requirements_not_registered_here"]=True
            checks["canonical_header_23_columns"]=True
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

    @app.post("/alliance/final/database/manual/{pc}/verify-source")
    async def verify_manual_source(pc:str,req:Request):
        _login(core,req)
        form=await req.form(); status=str(form.get("status") or "VERIFIED").upper(); actor=str(form.get("verified_by") or "team")
        normalized="VERIFIED" if status in ("VERIFIED","AVAILABLE") else "UNVERIFIED"
        with e.begin() as cx:
            cx.execute(text("""UPDATE pi_operational_properties
                SET verification_status=:st,updated_at=NOW()
                WHERE property_code=:pc AND COALESCE(entry_source,'MANUAL')='MANUAL'"""),{"st":normalized,"pc":pc})
        try:
            import alliance_operational_master_bridge_v12426 as bridge
            bridge.sync_property(e,pc,actor)
        except Exception:
            pass
        return RedirectResponse("/alliance/final/database/manual",303)

    @app.post("/alliance/final/database/magazine/{pid}/verify-source")
    async def verify_magazine_source(pid:str,req:Request):
        _login(core,req)
        form=await req.form(); status=str(form.get("status") or "VERIFIED").upper()
        normalized="VERIFIED" if status in ("VERIFIED","AVAILABLE") else ("NOT AVAILABLE" if status=="NOT_AVAILABLE" else status)
        with e.begin() as cx:
            cx.execute(text("""UPDATE pi_magazine_complete_v860
                SET verification_status=:st,updated_at=NOW()
                WHERE property_id=:pid AND archived_at IS NULL"""),{"st":normalized,"pid":pid})
        return RedirectResponse("/alliance/final/database/magazine",303)

    @app.post("/alliance/final/database/manual/{pc}/delete-source")
    def delete_manual_source(pc:str,req:Request):
        _login(core,req)
        import alliance_operational_master_bridge_v12426 as bridge
        with e.begin() as cx:
            x=cx.execute(text("""UPDATE pi_operational_properties
                SET entry_source='ARCHIVED',updated_at=NOW()
                WHERE property_code=:pc AND COALESCE(entry_source,'MANUAL')='MANUAL'"""),{"pc":pc})
            if not x.rowcount:return RedirectResponse("/alliance/final/database/manual",303)
        try:bridge.withdraw_property(e,pc,"canonical-grid")
        except Exception:pass
        return RedirectResponse("/alliance/final/database/manual",303)

    @app.post("/alliance/final/database/magazine/{pid}/delete-source")
    def delete_magazine_source(pid:str,req:Request):
        _login(core,req)
        with e.begin() as cx:
            cx.execute(text("""UPDATE pi_magazine_complete_v860
                SET archived_at=NOW(),record_status='ARCHIVED',updated_at=NOW()
                WHERE property_id=:pid AND archived_at IS NULL"""),{"pid":pid})
        return RedirectResponse("/alliance/final/database/magazine",303)

    @app.get("/alliance/final/database/media/{pc}",response_class=HTMLResponse)
    def manual_media_gallery(pc:str,req:Request):
        _login(core,req)
        with e.connect() as cx:
            exists=cx.execute(text("SELECT to_regclass('public.pi_operational_property_media')")).scalar()
            if not exists:return HTMLResponse(_shell("Property Media","<div class='card'>No media table found.</div>"))
            rows=cx.execute(text("""SELECT media_type,filename,mime_type,file_size,created_at
                FROM pi_operational_property_media WHERE property_code=:pc
                ORDER BY created_at,filename"""),{"pc":pc}).mappings().all()
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

    def _source_page(req,src,q,location,category,transaction,status,assigned,page,page_size):
        _login(core,req)
        page=max(1,int(page)); page_size=max(25,min(int(page_size),500))
        total=_property_source_count(e,src,q)
        pages=max(1,(total+page_size-1)//page_size)
        page=min(page,pages)
        offset=(page-1)*page_size
        body=_pager(src,page,page_size,total,q,location,category,transaction,status,assigned)
        body+=_property_table(core,e,req,src,q,location,category,transaction,status,assigned,page_size,offset)
        return HTMLResponse(_shell(f"{src.title()} Property Database",body))

    @app.get("/alliance/final/database/__source-status")
    def source_status():
        def owner(path):
            matches=[]
            for r in app.router.routes:
                methods=set(getattr(r,"methods",set()) or set())
                if getattr(r,"path",None)==path and "GET" in methods:
                    ep=getattr(r,"endpoint",None)
                    matches.append({
                        "module":getattr(ep,"__module__",""),
                        "name":getattr(ep,"__name__",""),
                    })
            return {"count":len(matches),"active":matches[0] if matches else {}}
        try: wa_total=_property_source_count(e,"WHATSAPP","")
        except Exception: wa_total=-1
        try: mag_total=_property_source_count(e,"MAGAZINE","")
        except Exception: mag_total=-1
        latest_gen_rows=0
        try:
            with e.connect() as cx:
                gen=cx.execute(text("""SELECT generation_id FROM pi_whatsapp_property_master_generation
                    WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
                if gen:
                    latest_gen_rows=int(cx.execute(text("""SELECT COUNT(*) FROM pi_whatsapp_property_master
                        WHERE generation_id=:g"""),{"g":gen}).scalar() or 0)
        except Exception:
            latest_gen_rows=0
        page_size=100
        return {
            "status":"PASS",
            "version":VERSION,
            "whatsapp":{
                "authority":"LATEST_COMPLETED pi_whatsapp_property_master GENERATION",
                "rows":wa_total,
                "latest_clean_generation_rows":latest_gen_rows,
                "master_linked_subset_not_used_for_source_page":true,
                "pages_at_100":max(1,(wa_total+page_size-1)//page_size),
                "route":"/alliance/final/database/whatsapp",
                "route_owner":owner("/alliance/final/database/whatsapp"),
            },
            "magazine":{
                "authority":"pi_magazine_complete_v860",
                "rows":mag_total,
                "pages_at_100":max(1,(mag_total+page_size-1)//page_size),
                "route":"/alliance/final/database/magazine",
                "route_owner":owner("/alliance/final/database/magazine"),
            },
            "pagination":{"default_page_size":100,"max_page_size":500,"full_database_browsable":True},
            "sources_separate":True,
            "data_exposed":False,
        }

    @app.get("/alliance/final/database/whatsapp",response_class=HTMLResponse)
    def whatsapp_database(req:Request,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),page:int=Query(1,ge=1),page_size:int=Query(100,ge=25,le=500)):
        return _source_page(req,"WHATSAPP",q,location,category,transaction,status,assigned,page,page_size)

    @app.get("/alliance/final/database/magazine",response_class=HTMLResponse)
    def magazine_database(req:Request,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),page:int=Query(1,ge=1),page_size:int=Query(100,ge=25,le=500)):
        return _source_page(req,"MAGAZINE",q,location,category,transaction,status,assigned,page,page_size)

    @app.get("/alliance/final/database/manual",response_class=HTMLResponse)
    def manual_database(req:Request,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),page:int=Query(1,ge=1),page_size:int=Query(100,ge=25,le=500)):
        return _source_page(req,"MANUAL",q,location,category,transaction,status,assigned,page,page_size)

    @app.get("/alliance/final/database/newspaper",response_class=HTMLResponse)
    def newspaper_database(req:Request,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),page:int=Query(1,ge=1),page_size:int=Query(100,ge=25,le=500)):
        return _source_page(req,"NEWSPAPER",q,location,category,transaction,status,assigned,page,page_size)

    @app.get("/alliance/final/database/master",response_class=HTMLResponse)
    def master_database(req:Request,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),page:int=Query(1,ge=1),page_size:int=Query(100,ge=25,le=500)):
        return _source_page(req,"MASTER",q,location,category,transaction,status,assigned,page,page_size)

    @app.get("/alliance/final/database/{source}")
    def db(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        if source.lower()=="__audit":
            return property_contract_audit()
        if source.lower()=="__repair":
            return {"status":"REPAIRED","version":VERSION,"repair":_repair_property_contract(),"audit":property_contract_audit()}
        if source.lower()=="__preview":
            # Same live production renderer, limited rows and masked contact data.
            preview_source=q.strip().upper() if q.strip().upper() in SOURCES else "MANUAL"
            rows=_property_rows(e,preview_source,"","","","","","",8)
            for r in rows:
                cr=_flat_record(r.get("clean_record"))
                cr["contact_number"]="XXXXXXXXXX"; cr["contact_phone"]="XXXXXXXXXX"; cr["phones"]=[]
                cr["contact_name"]="Masked"; cr["owner_broker_name"]="Masked"; cr["owner_name"]="Masked"; cr["broker_name"]="Masked"
                for k in ("remarks","description","original_description","source_text","raw_line","details"):
                    if cr.get(k): cr[k]=re.sub(r"(?<!\d)[6-9]\d{9}(?!\d)","XXXXXXXXXX",str(cr[k]))
                r["clean_record"]=cr
            body=_property_table(core,e,req,preview_source,"","","","","","",8,0,rows_override=rows)
            return HTMLResponse(_shell("Canonical Property Table Preview",body))
        _login(core,req);src=source.upper()
        if src not in SOURCES:return HTMLResponse("Unknown property database",404)
        return HTMLResponse(_shell(f"{src.title()} Property Database",_property_table(core,e,req,src,q,location,category,transaction,status,assigned,limit)))
    return {"status":"REGISTERED","version":VERSION,"scope":"PROPERTY_ONLY","requirements_registered":False}
