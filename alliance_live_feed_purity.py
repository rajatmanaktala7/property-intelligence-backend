from __future__ import annotations
import hashlib,re,unicodedata
from typing import Any,List
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="LIVE-FEED-PURITY-2.6-SAFE-LIVE"

REQ=[r"\bwanted\b",r"\brequirement\b",r"\brequired\b",r"\blooking for\b",r"\bneed(?:ed)?\b",r"\bclient budget\b",r"\btenant meeting\b",r"\bimmediate required\b"]
INV=[r"\bavailable for rent\b",r"\bavailable for sale\b",r"\bavailable on sale\b",r"\bfor rent\b",r"\bfor sale\b",r"\bto[- ]?let\b",r"\basking\b",r"\bdemand\b",r"\brent\b",r"\blease\b",r"\bsale\b",r"\bbhk\b",r"\bsq\.?\s*ft\b",r"\bsqft\b",r"\bsq\.?\s*yd",r"\bsqyd",r"\bvilla\b",r"\bapartment\b",r"\bflat\b",r"\bkothi\b",r"\bplot\b",r"\boffice\b",r"\bshop\b",r"\bshowroom\b",r"\bcommercial\b",r"\bwarehouse\b",r"\bpre[- ]?rented\b",r"\bpre[- ]?leased\b"]
NOISE=[r"\bgood morning\b",r"\bgood night\b",r"\bgood evening\b",r"\bhappy birthday\b",r"रक्षाबंधन",r"शुभकामनाएं",r"शुभरात्रि",r"music lovers",r"please remove such content",r"keep this group",r"instagram\.com",r"youtube\.com",r"facebook\.com/share"]

def norm(v:Any)->str:
    s=unicodedata.normalize("NFKC",str(v or "")).replace("\r\n","\n").replace("\r","\n")
    s=re.sub(r"[ \t]+"," ",s);s=re.sub(r"\n{3,}","\n\n",s)
    return s.strip()
def one(v:Any)->str:return re.sub(r"\s+"," ",norm(v)).strip()
def hits(ps,t):return sum(1 for p in ps if re.search(p,norm(t).lower(),re.I))
def phones(t):
    out=[]
    for m in re.finditer(r"(?:\+?91[\s\-]*)?[6-9](?:[\s\-]*\d){9}",norm(t)):
        d=re.sub(r"\D","",m.group(0))
        if len(d)==12 and d.startswith("91"):d=d[2:]
        if len(d)==11 and d.startswith("0"):d=d[1:]
        if len(d)==10:
            p="+91"+d
            if p not in out:out.append(p)
    return out
def classify(t):
    low=norm(t).lower();rq=hits(REQ,t);iv=hits(INV,t);nz=hits(NOISE,t)
    if rq:return "PROPERTY_REQUIREMENT"
    facts=sum([
      bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:cr|crore|lakh|lac|k)\b",low)),
      bool(re.search(r"\b\d{2,6}\s*(?:sq\.?\s*ft|sqft|sq\.?\s*yds?|sqyd|yards?|gaj)\b",low)),
      bool(re.search(r"\b\d+\s*bhk\b",low)),bool(phones(t))])
    if iv and iv+facts>=2:return "PROPERTY_INVENTORY"
    if nz or (len(one(t))<18 and not phones(t)):return "REJECTED"
    return "REVIEW"

def is_loc(line):
    x=one(line).lower()
    return bool(re.fullmatch(r"(?:[a-z]\s*block|[a-z]-block|g\.?k\.?\s*[- ]?\d|dlf\s*phase\s*\d|sushant\s*lok\s*\d|sector\s*[- ]?\d+)",x,re.I))
def numbered(line):
    x=one(line)
    return bool(re.match(r"^(?:\d{1,2}[.)]\s+|[1-9][️⃣⃣]\s*)",x))
def heading(line):
    x=one(line).strip("*_ ");low=x.lower()
    if not x or len(x)>70 or any(k in low for k in ["contact","regards","director","call","mobile","rent:","price:","asking:","demand:"]):return False
    if any(w in low for w in ["estate","city","tower","towers","park","palms","oasis","drive","harmony","atlantis","gallery","central","street","court","lagoon","residency","enclave","colony","heights","plaza","mall","escape","belveder","belvedere"]):return True
    alpha=re.sub(r"[^A-Za-z]","",x)
    return bool(alpha and len(alpha)>=5 and alpha.upper()==alpha and len(x.split())<=8)
def facts(line):
    low=one(line).lower()
    return bool(re.search(r"\b\d+\s*bhk\b",low) or re.search(r"\b\d{2,6}\s*(?:sq\.?\s*ft|sqft|sq\.?\s*yds?|sqyd|yards?|gaj)\b",low) or re.search(r"\b(?:rent|price|asking|demand)\s*[:@-]?\s*(?:₹|rs\.?)?\s*\d",low) or re.search(r"\b\d+(?:\.\d+)?\s*(?:cr|crore|lakh|lac)\b",low))
def header(lines):return [x for x in lines[:6] if any(k in one(x).lower() for k in ["for sale","for rent","available","inventory","deal available","pre rented","pre-rented","pre leased","pre-leased","kothi for sale"])][-2:]
def footer(lines):
    z=[]
    for x in lines[-10:]:
        low=one(x).lower()
        if re.search(r"\b(contact|call|mob|mobile|regards|director|coo|for visits|more details)\b",low) or phones(x):z.append(x)
    return z[-4:]
def make_blocks(lines,starts):
    h=header(lines);f=footer(lines);out=[]
    for n,s in enumerate(starts):
        e=starts[n+1] if n+1<len(starts) else len(lines)
        body=[x for x in lines[s:e] if not re.fullmatch(r"[-_=━─—\s]{3,}",one(x) or "")]
        if any(facts(x) for x in body):out.append(h+body+f)
    return out
def split_property_entities(t):
    t=norm(t)
    if classify(t)!="PROPERTY_INVENTORY":return [t]
    lines=[one(x) for x in t.split("\n") if one(x)]
    if len(lines)<=5:return [t]
    blocks=None
    starts=[i for i,x in enumerate(lines) if numbered(x)]
    if len(starts)>=2:blocks=make_blocks(lines,starts)
    if not blocks:
        starts=[i for i,x in enumerate(lines) if is_loc(x)]
        if len(starts)>=2:blocks=make_blocks(lines,starts)
    if not blocks:
        starts=[i for i,x in enumerate(lines) if re.search(r"^(?:\*+)?for sale\s*:?",one(x),re.I)]
        if len(starts)>=2:blocks=make_blocks(lines,starts)
    if not blocks:
        starts=[]
        for i,x in enumerate(lines):
            if heading(x) and any(facts(y) for y in lines[i+1:i+5]):starts.append(i)
        starts=[x for j,x in enumerate(starts) if j==0 or x-starts[j-1]>=2]
        if len(starts)>=2:blocks=make_blocks(lines,starts)
    if not blocks:return [t]
    out=[];seen=set()
    for b in blocks:
        x="\n".join(b).strip()
        if classify(x)!="PROPERTY_INVENTORY":continue
        f=re.sub(r"\W+","",x.lower())
        if f not in seen:seen.add(f);out.append(x)
    return out if len(out)>=2 else [t]

def ensure(engine):
    stmts=["""CREATE TABLE IF NOT EXISTS alliance_live_feed_entities(
    id BIGSERIAL PRIMARY KEY,source_event_id TEXT NOT NULL,entity_index INTEGER NOT NULL DEFAULT 1,
    entity_code TEXT UNIQUE NOT NULL,classification TEXT NOT NULL,raw_message TEXT NOT NULL,
    entity_text TEXT NOT NULL,source_group TEXT,sender_name TEXT,sender_phone TEXT,contact_phones TEXT,
    canonical_property_code TEXT,entity_fingerprint TEXT,status TEXT DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_event_id,entity_index))""",
    "ALTER TABLE alliance_live_feed_entities ADD COLUMN IF NOT EXISTS entity_fingerprint TEXT",
    "CREATE INDEX IF NOT EXISTS idx_live_feed_entities_class ON alliance_live_feed_entities(classification,status)",
    "CREATE INDEX IF NOT EXISTS idx_live_feed_entities_fp ON alliance_live_feed_entities(entity_fingerprint)"]
    with engine.begin() as c:
        for s in stmts:c.execute(text(s))
def _wa_columns(c, table):
    rows=c.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t
        ORDER BY ordinal_position
    """),{"t":table}).scalars().all()
    return [str(x) for x in rows]

def _pick(cols, options):
    lower={x.lower():x for x in cols}
    for o in options:
        if o in lower:
            return lower[o]
    return None

def event_rows(core,limit=3000):
    """
    Final source behavior:
    1. Use the same separate WHATSAPP_DATABASE_URL as the working V2 adapter.
    2. Prefer ORIGINAL wai_raw_messages so line breaks / multi-property structure survive.
    3. Dynamically discover the raw-message text column.
    4. Enrich sender/group from wai_listings where possible.
    5. Fall back to wai_listings only if raw messages cannot be read.
    """
    import alliance_v2_whatsapp_adapter as wa_adapter
    source_engine,should_dispose=wa_adapter._source_engine(core.engine)
    try:
        with source_engine.connect() as c:
            raw_exists=bool(c.execute(text("SELECT to_regclass('public.wai_raw_messages') IS NOT NULL")).scalar())
            if raw_exists:
                cols=_wa_columns(c,"wai_raw_messages")
                text_col=_pick(cols,[
                    "raw_text","raw_message","message_text","message","text","body",
                    "content","message_body","payload_text","plain_text"
                ])
                id_col=_pick(cols,["id","message_id","raw_message_id","event_id"])
                date_col=_pick(cols,["sent_at","created_at","received_at","timestamp","message_time"])
                phone_col=_pick(cols,["sender_phone","phone","from_phone","contact_phone"])
                name_col=_pick(cols,["sender_display_name","sender_name","display_name","contact_name"])

                if text_col:
                    id_expr=f'rm."{id_col}"::text' if id_col else "row_number() over ()::text"
                    date_expr=f'rm."{date_col}"' if date_col else "NOW()"
                    phone_expr=f'COALESCE(rm."{phone_col}"::text,\'\')' if phone_col else "''"
                    name_expr=f'COALESCE(rm."{name_col}"::text,\'\')' if name_col else "''"

                    # Group name is normally held on the derived listing, linked by source_message_id.
                    list_exists=bool(c.execute(text("SELECT to_regclass('public.wai_listings') IS NOT NULL")).scalar())
                    if list_exists and id_col:
                        sql=f"""
                            SELECT
                              'RAW-' || {id_expr} AS source_event_id,
                              COALESCE(MAX(l.source_group_name),'') AS source_group,
                              COALESCE(NULLIF({name_expr},''),MAX(l.poster_name),'') AS sender_name,
                              COALESCE(NULLIF({phone_expr},''),MAX(ct.phone),'') AS sender_phone,
                              rm."{text_col}"::text AS raw_message,
                              {date_expr} AS created_at
                            FROM wai_raw_messages rm
                            LEFT JOIN wai_listings l ON l.source_message_id=rm."{id_col}"
                            LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
                            WHERE NULLIF(BTRIM(rm."{text_col}"::text),'') IS NOT NULL
                            GROUP BY rm."{id_col}", rm."{text_col}", {date_expr}, {phone_expr}, {name_expr}
                            ORDER BY {date_expr} DESC NULLS LAST
                            LIMIT :lim
                        """
                    else:
                        sql=f"""
                            SELECT
                              'RAW-' || {id_expr} AS source_event_id,
                              '' AS source_group,
                              {name_expr} AS sender_name,
                              {phone_expr} AS sender_phone,
                              rm."{text_col}"::text AS raw_message,
                              {date_expr} AS created_at
                            FROM wai_raw_messages rm
                            WHERE NULLIF(BTRIM(rm."{text_col}"::text),'') IS NOT NULL
                            ORDER BY {date_expr} DESC NULLS LAST
                            LIMIT :lim
                        """
                    try:
                        rows=c.execute(text(sql),{"lim":limit}).mappings().all()
                        if rows:
                            return rows
                    except Exception:
                        pass

            # Fallback: normalized listings.
            if c.execute(text("SELECT to_regclass('public.wai_listings') IS NOT NULL")).scalar():
                rows=c.execute(text("""
                    SELECT
                      'LISTING-' || l.id::text AS source_event_id,
                      COALESCE(l.source_group_name,'') AS source_group,
                      COALESCE(ct.display_name,l.poster_name,'') AS sender_name,
                      COALESCE(ct.phone,rm.sender_phone,'') AS sender_phone,
                      COALESCE(NULLIF(l.raw_listing_text,''),NULLIF(l.summary,''),'') AS raw_message,
                      COALESCE(l.created_at,rm.sent_at,NOW()) AS created_at
                    FROM wai_listings l
                    LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
                    LEFT JOIN wai_raw_messages rm ON rm.id=l.source_message_id
                    WHERE NULLIF(BTRIM(COALESCE(NULLIF(l.raw_listing_text,''),NULLIF(l.summary,''),'')),'') IS NOT NULL
                    ORDER BY COALESCE(l.created_at,rm.sent_at) DESC NULLS LAST,l.id DESC
                    LIMIT :lim
                """),{"lim":limit}).mappings().all()
                if rows:
                    return rows
    finally:
        if should_dispose:
            source_engine.dispose()
    return []

def source_diagnostics(core):
    import os
    import alliance_v2_whatsapp_adapter as wa_adapter
    source_engine,should_dispose=wa_adapter._source_engine(core.engine)
    out={
        "whatsapp_database_url_configured":bool((os.getenv("WHATSAPP_DATABASE_URL") or "").strip()),
        "using_separate_whatsapp_database":bool(should_dispose),
        "raw_source_preferred":True,
        "wai_raw_messages_exists":False,
        "wai_raw_messages_count":0,
        "wai_raw_text_column":None,
        "wai_listings_count":0,
    }
    try:
        with source_engine.connect() as c:
            if c.execute(text("SELECT to_regclass('public.wai_raw_messages') IS NOT NULL")).scalar():
                out["wai_raw_messages_exists"]=True
                out["wai_raw_messages_count"]=int(c.execute(text("SELECT COUNT(*) FROM wai_raw_messages")).scalar() or 0)
                cols=_wa_columns(c,"wai_raw_messages")
                out["wai_raw_text_column"]=_pick(cols,[
                    "raw_text","raw_message","message_text","message","text","body",
                    "content","message_body","payload_text","plain_text"
                ])
            if c.execute(text("SELECT to_regclass('public.wai_listings') IS NOT NULL")).scalar():
                out["wai_listings_count"]=int(c.execute(text("SELECT COUNT(*) FROM wai_listings")).scalar() or 0)
    except Exception as exc:
        out["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        if should_dispose:
            source_engine.dispose()
    return out

def efp(t):
    x=one(t).lower();x=re.sub(r"\+?91?\d[\d\s\-]{8,}","",x);x=re.sub(r"\W+"," ",x)
    return hashlib.sha1(x.encode()).hexdigest()
def sync(core,limit=3000):
    import alliance_v383_database_foundation as canon
    ensure(core.engine)
    # Rebuild only the visible purity staging layer. Canonical/legacy source data is untouched.
    with core.engine.begin() as _c:
        _c.execute(text("UPDATE alliance_live_feed_entities SET status='STALE' WHERE status='ACTIVE'"))
    counts={"source_events":0,"entities":0,"inventory":0,"requirements":0,"rejected":0,"review":0}
    for ev in reversed(event_rows(core,limit)):
        raw=norm(ev["raw_message"]);parts=split_property_entities(raw) if classify(raw)=="PROPERTY_INVENTORY" else [raw]
        with core.engine.begin() as c:
            aliases=list(c.execute(text("SELECT alias_text,canonical_location FROM alliance_location_aliases WHERE approved=TRUE ORDER BY length(alias_text) DESC")).mappings())
            for idx,entity in enumerate(parts,1):
                cls=classify(entity);fp=efp(entity);code="LFE-"+hashlib.sha1(f"{ev['source_event_id']}|{idx}|{fp}".encode()).hexdigest()[:12].upper()
                ph=phones(entity);pc=None
                if cls=="PROPERTY_INVENTORY":
                    low=norm(entity).lower();tx="SALE" if re.search(r"\b(for sale|sale|asking|demand|outright)\b",low) else ("RENT" if re.search(r"\b(for rent|rent|lease|to[- ]?let)\b",low) else "UNKNOWN")
                    pt="Residential" if re.search(r"\b(bhk|flat|apartment|villa|kothi|builder floor)\b",low) else ("Office" if "office" in low else ("Commercial Shop" if re.search(r"\b(shop|showroom|retail)\b",low) else "Property"))
                    loc="UNKNOWN"
                    for a in aliases:
                        if a["alias_text"] and a["alias_text"].lower() in low:loc=a["canonical_location"];break
                    am=re.search(r"\b(\d{2,6})\s*(?:sq\.?\s*ft|sqft)\b",low);area=float(am.group(1)) if am else None
                    pc=canon._upsert_property(c,{"property_name":one(entity)[:220],"location":loc,"city":"Delhi NCR","building_project":one(entity)[:160],"property_type":pt,"transaction_type":tx,"area_sqft":area,"floor":None,"intended_use_tags":pt})
                    lc=canon._upsert_listing(c,pc,{"source_type":"WHATSAPP","source_table":"alliance_live_feed_entities","source_record_id":code,"source_name":ev["source_group"] or "WhatsApp","raw_text":entity,"availability_status":"UNKNOWN","verification_status":"UNVERIFIED","verification_confidence":0,"captured_at":ev["created_at"]})
                    if ph:canon._upsert_contact(c,lc,ev["sender_name"],ph[0],"BROKER",True)
                    counts["inventory"]+=1
                elif cls=="PROPERTY_REQUIREMENT":counts["requirements"]+=1
                elif cls=="REJECTED":counts["rejected"]+=1
                else:counts["review"]+=1
                c.execute(text("""INSERT INTO alliance_live_feed_entities(source_event_id,entity_index,entity_code,classification,raw_message,entity_text,source_group,sender_name,sender_phone,contact_phones,canonical_property_code,entity_fingerprint,status,created_at,updated_at)
                VALUES(:sid,:idx,:code,:cls,:raw,:entity,:grp,:sn,:sp,:phones,:pc,:fp,'ACTIVE',COALESCE(:created,NOW()),NOW())
                ON CONFLICT(source_event_id,entity_index) DO UPDATE SET entity_code=EXCLUDED.entity_code,classification=EXCLUDED.classification,raw_message=EXCLUDED.raw_message,entity_text=EXCLUDED.entity_text,source_group=EXCLUDED.source_group,sender_name=EXCLUDED.sender_name,sender_phone=EXCLUDED.sender_phone,contact_phones=EXCLUDED.contact_phones,canonical_property_code=EXCLUDED.canonical_property_code,entity_fingerprint=EXCLUDED.entity_fingerprint,status='ACTIVE',updated_at=NOW()"""),
                {"sid":str(ev["source_event_id"]),"idx":idx,"code":code,"cls":cls,"raw":raw,"entity":entity,"grp":ev["source_group"],"sn":ev["sender_name"],"sp":ev["sender_phone"],"phones":" | ".join(ph),"pc":pc,"fp":fp,"created":ev["created_at"]})
                counts["entities"]+=1
        counts["source_events"]+=1
    return {"status":"OK","version":VERSION,**counts}
def rows(core,q="",limit=1000):
    ensure(core.engine);p={"lim":limit};w=["classification='PROPERTY_INVENTORY'","status='ACTIVE'"]
    if q.strip():w.append("(entity_text ILIKE :q OR source_group ILIKE :q OR sender_name ILIKE :q OR contact_phones ILIKE :q)");p["q"]="%"+q.strip()+"%"
    with core.engine.connect() as c:return c.execute(text(f"SELECT entity_code,created_at,source_group,sender_name,contact_phones,entity_text,canonical_property_code FROM alliance_live_feed_entities WHERE {' AND '.join(w)} ORDER BY created_at DESC,id DESC LIMIT :lim"),p).mappings().all()
def esc(v):return str(v or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def safe_source_status(core):
    """Fast read-only source diagnostics. Never runs sync/canonical writes."""
    try:
        d=source_diagnostics(core)
    except Exception as exc:
        d={"error":f"{type(exc).__name__}: {exc}"}
    try:
        ensure(core.engine)
        with core.engine.connect() as c:
            d["active_staged_entities"]=int(c.execute(text(
                "SELECT COUNT(*) FROM alliance_live_feed_entities WHERE status='ACTIVE'"
            )).scalar() or 0)
            d["active_inventory_entities"]=int(c.execute(text(
                "SELECT COUNT(*) FROM alliance_live_feed_entities WHERE status='ACTIVE' AND classification='PROPERTY_INVENTORY'"
            )).scalar() or 0)
    except Exception as exc:
        d["staging_error"]=f"{type(exc).__name__}: {exc}"
    return {"status":"OK","version":VERSION,"mode":"SAFE_READ_ONLY","source_diagnostics":d}

def safe_raw_rows(core, limit=250):
    """
    Read original WhatsApp messages with NO joins and NO writes.
    This is intentionally small and fast for the visible page.
    """
    import alliance_v2_whatsapp_adapter as wa_adapter
    source_engine,should_dispose=wa_adapter._source_engine(core.engine)
    try:
        with source_engine.connect() as c:
            if not c.execute(text("SELECT to_regclass('public.wai_raw_messages') IS NOT NULL")).scalar():
                return []
            cols=_wa_columns(c,"wai_raw_messages")
            text_col=_pick(cols,[
                "raw_text","raw_message","message_text","message","text","body",
                "content","message_body","payload_text","plain_text"
            ])
            if not text_col:
                return []
            id_col=_pick(cols,["id","message_id","raw_message_id","event_id"])
            date_col=_pick(cols,["sent_at","created_at","received_at","timestamp","message_time"])
            phone_col=_pick(cols,["sender_phone","phone","from_phone","contact_phone"])
            name_col=_pick(cols,["sender_display_name","sender_name","display_name","contact_name"])
            group_col=_pick(cols,["group_name","source_group_name","chat_name","group_title","source_group"])

            id_expr=f'"{id_col}"::text' if id_col else "row_number() over ()::text"
            date_expr=f'"{date_col}"' if date_col else "NOW()"
            phone_expr=f'COALESCE("{phone_col}"::text,\'\')' if phone_col else "''"
            name_expr=f'COALESCE("{name_col}"::text,\'\')' if name_col else "''"
            group_expr=f'COALESCE("{group_col}"::text,\'\')' if group_col else "''"

            sql=f"""
                SELECT
                  {id_expr} AS source_event_id,
                  {group_expr} AS source_group,
                  {name_expr} AS sender_name,
                  {phone_expr} AS sender_phone,
                  "{text_col}"::text AS raw_message,
                  {date_expr} AS created_at
                FROM wai_raw_messages
                WHERE NULLIF(BTRIM("{text_col}"::text),'') IS NOT NULL
                ORDER BY {date_expr} DESC NULLS LAST
                LIMIT :lim
            """
            return c.execute(text(sql),{"lim":max(1,min(int(limit),500))}).mappings().all()
    finally:
        if should_dispose:
            source_engine.dispose()

def safe_live_entities(core, limit=250):
    """
    Split/filter only in memory for page rendering.
    No INSERT/UPDATE/UPSERT and no canonical database writes.
    """
    out=[]
    for ev in safe_raw_rows(core,limit):
        raw=norm(ev["raw_message"])
        cls=classify(raw)
        if cls!="PROPERTY_INVENTORY":
            continue
        parts=split_property_entities(raw)
        for i,entity in enumerate(parts,1):
            if classify(entity)!="PROPERTY_INVENTORY":
                continue
            out.append({
                "source_event_id":ev["source_event_id"],
                "entity_index":i,
                "created_at":ev["created_at"],
                "source_group":ev["source_group"],
                "sender_name":ev["sender_name"],
                "sender_phone":ev["sender_phone"],
                "entity_text":entity,
                "contact_phones":" | ".join(phones(entity) or ([ev["sender_phone"]] if ev["sender_phone"] else [])),
            })
    return out

def register(wrapped):
    app=wrapped.app
    core=wrapped.core

    owned={"/whatsapp-live/feed","/api/live-feed-purity/status","/api/live-feed-purity/sample"}
    app.router.routes[:]=[
        r for r in app.router.routes
        if not (getattr(r,"path",None) in owned and "GET" in (getattr(r,"methods",set()) or set()))
    ]

    def status():
        return safe_source_status(core)

    def sample():
        rr=safe_raw_rows(core,25)
        entity_count=0
        inventory_messages=0
        for ev in rr:
            raw=norm(ev["raw_message"])
            if classify(raw)=="PROPERTY_INVENTORY":
                inventory_messages+=1
                entity_count+=len(split_property_entities(raw))
        return {
            "status":"OK",
            "version":VERSION,
            "mode":"SAFE_READ_ONLY",
            "raw_messages_sampled":len(rr),
            "inventory_messages":inventory_messages,
            "property_entities_after_split":entity_count,
        }

    def feed(request:Request):
        q=str(request.query_params.get("q") or "").strip().lower()
        try:
            lim=int(request.query_params.get("limit") or 250)
        except Exception:
            lim=250
        ents=safe_live_entities(core,max(25,min(lim,500)))
        if q:
            ents=[
                x for x in ents
                if q in one(x.get("entity_text")).lower()
                or q in one(x.get("source_group")).lower()
                or q in one(x.get("sender_name")).lower()
                or q in one(x.get("contact_phones")).lower()
            ]

        trs="".join(
            f"""<tr>
            <td>{esc(x['created_at'])}</td>
            <td>{esc(x['source_group'] or '—')}</td>
            <td>{esc(x['sender_name'] or '—')}</td>
            <td style='min-width:520px;white-space:pre-wrap'>{esc(x['entity_text'])}</td>
            <td>{esc(x['contact_phones'] or '—')}</td>
            </tr>"""
            for x in ents[:750]
        )

        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Clean Live Property Feed</title>
        <style>
        body{{font-family:Arial;background:#f6f2ec;margin:0;color:#2f2923}}
        header{{background:#5a4635;color:white;padding:18px 24px}}
        main{{padding:18px;max-width:1800px;margin:auto}}
        .card{{background:white;border:1px solid #ddd0c2;border-radius:10px;padding:12px;margin-bottom:12px}}
        table{{width:100%;border-collapse:collapse;background:white}}
        th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;font-size:12px}}
        th{{background:#f2e8dc;position:sticky;top:0}}
        input{{width:75%;padding:10px}}button{{padding:10px}}
        </style></head><body>
        <header><h2 style='margin:0'>Clean Live Property Feed</h2>
        <small>Safe live mode · original WhatsApp messages · one property per entity · no database writes during page load</small></header>
        <main><div class='card'>
        <form><input name='q' value='{esc(q)}' placeholder='Search project, area, broker or phone'>
        <input type='hidden' name='limit' value='{lim}'><button>Search</button></form>
        <p><b>{len(ents)}</b> clean property entities generated from the latest raw WhatsApp messages.</p>
        </div><div class='card' style='overflow:auto'>
        <table><tr><th>Received</th><th>Group</th><th>Sender</th><th>Property Entity</th><th>Contact</th></tr>
        {trs or '<tr><td colspan=5>No property inventory found in the current sample.</td></tr>'}
        </table></div></main></body></html>""")

    app.add_api_route("/api/live-feed-purity/status",status,methods=["GET"])
    app.add_api_route("/api/live-feed-purity/sample",sample,methods=["GET"])
    app.add_api_route("/whatsapp-live/feed",feed,methods=["GET"])
    return {"status":"REGISTERED","version":VERSION,"mode":"SAFE_READ_ONLY"}
