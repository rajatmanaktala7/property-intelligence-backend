from __future__ import annotations
import html, json, re
from urllib.parse import quote
from fastapi import HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import inspect, text

VERSION="2.1.0-MULTI-SELECT-MESSAGE-VERIFY-UX"
MASTER_REQUIREMENT_TABLE="pi_requirement_gate_v1191"
MASTER_PROPERTY_TABLE="pi_master_properties_v711"
MASTER_LINKS_TABLE="pi_master_source_links_v711"
WORKSPACE_ROUTE="/alliance/master-requirement-matcher"
SMART_MATCHER_ROUTE="/alliance/primary/matcher"

def _app(core): return getattr(core,"app",None) or core

def _auth(core,request:Request):
    try: core.need_login(request)
    except Exception as exc: raise HTTPException(status_code=401,detail="Login required") from exc

def _e(v): return html.escape(str(v or ""))
def _norm(v): return re.sub(r"\s+"," ",str(v or "").replace("\u00a0"," ")).strip()

def _phone(v):
    raw=str(v or "").strip()
    digits=re.sub(r"\D+","",raw)
    if digits.startswith("00"): digits=digits[2:]
    if len(digits)==12 and digits.startswith("91"): digits=digits[-10:]
    if len(digits)==11 and digits.startswith("0"): digits=digits[-10:]
    # India operational contact rule. Opaque WhatsApp LID/JID numeric IDs
    # are commonly 13-15 digits and must never be rendered as phone numbers.
    if len(digits)==10 and digits[0] in "6789":
        return digits
    return ""

def _sender_identity(sender_phone="", sender_name="", sender_jid=""):
    raw_phone=_norm(sender_phone)
    raw_name=_norm(sender_name)
    raw_jid=_norm(sender_jid)
    jid_local=raw_jid.split("@",1)[0] if "@" in raw_jid else raw_jid
    jid_domain=raw_jid.split("@",1)[1].lower() if "@" in raw_jid else ""

    # 1) phone-shaped value in the sender_phone field
    p=_phone(raw_phone)
    if p:
        return {"phone":p,"name":raw_name,"sender_id":"","status":"PHONE_FROM_SENDER_PHONE"}

    # 2) some exports put the actual formatted number in sender/display name
    p=_phone(raw_name)
    if p:
        return {"phone":p,"name":raw_name,"sender_id":"","status":"PHONE_FROM_SENDER_NAME"}

    # 3) only traditional user JIDs are allowed to yield a phone number.
    # @lid, group and other opaque identifiers remain IDs, never contacts.
    if jid_domain in ("s.whatsapp.net","c.us"):
        p=_phone(jid_local)
        if p:
            return {"phone":p,"name":raw_name,"sender_id":"","status":"PHONE_FROM_USER_JID"}

    opaque=raw_jid or raw_phone
    return {"phone":"","name":raw_name,"sender_id":opaque,"status":"OPAQUE_SENDER_ID"}

def _json_list(v):
    if isinstance(v,list): return v
    if v in (None,""): return []
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,list) else [x]
        except Exception: return [v]
    return [v]

def _category(source_type,source_table):
    s=f"{_norm(source_type)} {_norm(source_table)}".upper()
    if "WHATSAPP" in s or re.search(r"\bWA_",s): return "WHATSAPP"
    if "NEWSPAPER" in s or "MAGAZINE" in s or "CAPTURE" in s: return "NEWSPAPER"
    if "DISCOVER" in s: return "DISCOVERY"
    if "MANUAL" in s or "FORM" in s: return "MANUAL"
    return "OTHER"

def _table_exists(engine,name): return name in set(inspect(engine).get_table_names())
def _cols(engine,table): return [c["name"] for c in inspect(engine).get_columns(table)]
def _first(cols,*names):
    low={c.lower():c for c in cols}
    for n in names:
        if n.lower() in low: return low[n.lower()]
    return None

def _wa_engine():
    try:
        import alliance_whatsapp_source_reconciliation_v2 as wr
        eng=wr._wa_engine()
        if eng is not None: return eng
    except Exception:
        pass
    try:
        import alliance_whatsapp_live_clean_os_v1 as clean
        for name in ("source_engine","wa_engine","engine"):
            eng=getattr(clean,name,None)
            if eng is not None: return eng
    except Exception:
        pass
    return None

def _requirements(core):
    if not _table_exists(core.engine,MASTER_REQUIREMENT_TABLE):
        raise RuntimeError(f"{MASTER_REQUIREMENT_TABLE} missing")
    sql=text(f"""
        SELECT id,evidence_key,source_type,source_table,source_pk,source_group,source_date,
               original_message,classification,genuine_confidence,transaction_type,
               property_category,intended_use,locations,alternate_locations,
               area_min_sqft,area_max_sqft,budget_min,budget_max,
               company_brand_person,contact_numbers,evidence_quality,matcher_eligible,
               created_at,updated_at
        FROM {MASTER_REQUIREMENT_TABLE}
        WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE')
        ORDER BY COALESCE(updated_at,created_at) DESC,id DESC
        LIMIT 20000
    """)
    out=[]
    with core.engine.connect() as c:
        for r in c.execute(sql).mappings():
            d=dict(r); d["category"]=_category(d.get("source_type"),d.get("source_table"))
            d["locations_list"]=_json_list(d.get("locations"))
            d["contacts_list"]=[_phone(x) or _norm(x) for x in _json_list(d.get("contact_numbers")) if _norm(x)]
            out.append(d)
    return out

def _wa_requirement_sender_map(rows):
    eng=_wa_engine()
    wanted={_norm(r.get("source_pk")) for r in rows if r.get("category")=="WHATSAPP" and _norm(r.get("source_pk"))}
    if not eng or not wanted or not _table_exists(eng,"wa_requirements"): return {}
    rc=_cols(eng,"wa_requirements")
    rid=_first(rc,"wa_requirement_id","id","requirement_id")
    mid=_first(rc,"message_id","source_message_id")
    cphone=_first(rc,"contact_phone","phone","mobile","contact_no")
    cname=_first(rc,"contact_name","client_name","company_name")
    ctype=_first(rc,"contact_type","role")
    sid=_first(rc,"source_id","group_id")
    if not rid: return {}
    fields=[f'"{rid}" AS rid']
    for alias,col in (("mid",mid),("contact_phone",cphone),("contact_name",cname),("contact_type",ctype),("source_id",sid)):
        fields.append((f'"{col}"' if col else "NULL")+f' AS "{alias}"')
    result={}
    vals=list(wanted)
    B=500
    with eng.connect() as c:
        for i in range(0,len(vals),B):
            chunk=vals[i:i+B]; params={f"p{j}":v for j,v in enumerate(chunk)}
            holders=",".join(":"+k for k in params)
            q=text(f'SELECT {",".join(fields)} FROM "wa_requirements" WHERE CAST("{rid}" AS TEXT) IN ({holders})')
            for rr in c.execute(q,params).mappings():
                d=dict(rr); result[str(d["rid"])]=d
    # Resolve actual WhatsApp sender from raw message ledger. Sender is provenance, not assumed owner/broker.
    if _table_exists(eng,"wa_messages"):
        mc=_cols(eng,"wa_messages")
        mmid=_first(mc,"message_id","id","wa_message_id")
        sender_phone=_first(mc,"sender_phone","phone_number","sender_number","author_phone")
        sender_name=_first(mc,"sender_name","sender_display_name","author_name","sender")
        sender_jid=_first(mc,"sender_jid","jid","author","participant","remote_jid")
        if mmid and sender_phone:
            mids={_norm(v.get("mid")) for v in result.values() if _norm(v.get("mid"))}
            mvals=list(mids)
            bymid={}
            with eng.connect() as c:
                for i in range(0,len(mvals),B):
                    chunk=mvals[i:i+B]; params={f"m{j}":v for j,v in enumerate(chunk)}
                    holders=",".join(":"+k for k in params)
                    sel=f'"{mmid}" AS mid,"{sender_phone}" AS sender_phone'
                    if sender_name: sel+=f',"{sender_name}" AS sender_name'
                    if sender_jid: sel+=f',"{sender_jid}" AS sender_jid'
                    q=text(f'SELECT {sel} FROM "wa_messages" WHERE CAST("{mmid}" AS TEXT) IN ({holders})')
                    bymid.update({str(x["mid"]):dict(x) for x in c.execute(q,params).mappings()})
            for d in result.values():
                md=bymid.get(_norm(d.get("mid")))
                if md:
                    ident=_sender_identity(md.get("sender_phone"),md.get("sender_name"),md.get("sender_jid"))
                    d["sender_phone"]=ident["phone"]
                    d["sender_name"]=ident["name"]
                    d["sender_id"]=ident["sender_id"]
                    d["sender_identity_status"]=ident["status"]
    # Safety: an account/device phone can be stamped onto many unrelated opaque
    # WhatsApp sender IDs by historical ingestion. A genuine sender phone must not
    # simultaneously identify multiple distinct opaque senders in this batch.
    phone_ids={}
    for d in result.values():
        p=_phone(d.get("sender_phone")); oid=_norm(d.get("sender_id"))
        if p and oid:
            phone_ids.setdefault(p,set()).add(oid)
    contaminated={p for p,ids in phone_ids.items() if len(ids)>=3}
    if contaminated:
        for d in result.values():
            p=_phone(d.get("sender_phone"))
            if p in contaminated:
                d["sender_phone"]=""
                d["sender_identity_status"]="REJECTED_SHARED_ACCOUNT_OR_DEVICE_PHONE"
    return result

def _resolve_sender_ids_from_registry(rows):
    try:
        from alliance_whatsapp_sender_identity_registry_v1 import resolve_many
        eng=_wa_engine()
        if eng is None:
            return rows
        mapping=resolve_many(eng,[r.get("whatsapp_sender_id") for r in rows])
        reverse={}
        for oid,pv in mapping.items():
            p=_phone(pv); oid=_norm(oid)
            if p and oid: reverse.setdefault(p,set()).add(oid)
        contaminated={p for p,ids in reverse.items() if len(ids)>=2}
        for r in rows:
            if _phone(r.get("whatsapp_sender_phone")):
                continue
            oid=_norm(r.get("whatsapp_sender_id"))
            p=_phone(mapping.get(oid))
            if p and p not in contaminated:
                r["whatsapp_sender_phone"]=p
                r["whatsapp_sender_status"]="REGISTRY_RESOLVED_UNIQUE_EXACT_EVIDENCE"
                if not r.get("contacts_list"):
                    r["contacts_list"]=[p]
                    r["contact_fallback"]="WHATSAPP_SENDER_REGISTRY"
            elif p in contaminated:
                r["whatsapp_sender_status"]="REJECTED_SHARED_REGISTRY_PHONE"
        return rows
    except Exception:
        return rows

def _apply_requirement_sender(rows):
    mp=_wa_requirement_sender_map(rows)
    for r in rows:
        meta=mp.get(_norm(r.get("source_pk"))) or {}
        r["whatsapp_sender_phone"]=_phone(meta.get("sender_phone"))
        r["whatsapp_sender_name"]=_norm(meta.get("sender_name"))
        r["whatsapp_sender_id"]=_norm(meta.get("sender_id"))
        r["whatsapp_sender_status"]=_norm(meta.get("sender_identity_status"))
        r["explicit_source_contact"]=_phone(meta.get("contact_phone"))
        if not r["contacts_list"] and r["explicit_source_contact"]:
            r["contacts_list"]=[r["explicit_source_contact"]]
        if not r["contacts_list"] and r["whatsapp_sender_phone"]:
            r["contacts_list"]=[r["whatsapp_sender_phone"]]
            r["contact_fallback"]="WHATSAPP_SENDER"
        else:
            r["contact_fallback"]=""
    return _resolve_sender_ids_from_registry(rows)

def _find_req(rows,rid):
    for r in rows:
        if str(r.get("id"))==str(rid): return r
    return None

def _master_contact_map(core,ids):
    ids=[str(x) for x in ids if str(x or "").strip()]
    if not ids or not _table_exists(core.engine,MASTER_PROPERTY_TABLE): return {}
    cols=_cols(core.engine,MASTER_PROPERTY_TABLE)
    idcol=_first(cols,"property_id","record_id","canonical_id","id")
    if not idcol:return {}
    contact_cols=[c for c in cols if c.lower() in {
        "contact_phone","contact_numbers","phone","mobile","owner_phone","broker_phone",
        "sender_phone","whatsapp_sender","contact_no","phone_number","phones","phone_numbers"}]
    source_cols=[c for c in cols if c.lower() in {"source","source_name","source_type","source_group"}]
    select=[f'"{idcol}" AS pid']+[f'"{c}" AS "{c}"' for c in contact_cols+source_cols]
    params={f"p{i}":v for i,v in enumerate(ids)}
    holders=",".join(":"+k for k in params)
    q=text(f'SELECT {",".join(select)} FROM "{MASTER_PROPERTY_TABLE}" WHERE CAST("{idcol}" AS TEXT) IN ({holders})')
    out={}
    with core.engine.connect() as c:
        for r in c.execute(q,params).mappings():
            d=dict(r); contacts=[]
            for col in contact_cols:
                for v in _json_list(d.get(col)):
                    sv=_phone(v) or _norm(v)
                    if sv and sv not in [x["value"] for x in contacts]:
                        contacts.append({"label":col,"value":sv})
            out[str(d.get("pid"))]={"contacts":contacts,"sources":[_norm(d.get(x)) for x in source_cols if _norm(d.get(x))]}
    return out

def _master_whatsapp_sender_map(core,ids):
    """Best-effort source lineage: master property -> source link -> wa_properties -> sender_phone.
    Never labels sender as owner/broker."""
    eng=_wa_engine()
    if not eng or not ids or not _table_exists(core.engine,MASTER_LINKS_TABLE) or not _table_exists(eng,"wa_properties"):
        return {}
    lc=_cols(core.engine,MASTER_LINKS_TABLE)
    master_col=_first(lc,"property_id","master_property_id","canonical_property_id","master_id","target_id")
    source_pk_col=_first(lc,"source_pk","source_id","source_record_id","record_id")
    source_type_col=_first(lc,"source_type","source_system","source")
    source_table_col=_first(lc,"source_table","table_name")
    if not master_col or not source_pk_col:return {}
    params={f"p{i}":str(v) for i,v in enumerate(ids)}
    holders=",".join(":"+k for k in params)
    fields=f'"{master_col}" AS master_id,"{source_pk_col}" AS source_pk'
    if source_type_col: fields+=f',"{source_type_col}" AS source_type'
    if source_table_col: fields+=f',"{source_table_col}" AS source_table'
    q=text(f'SELECT {fields} FROM "{MASTER_LINKS_TABLE}" WHERE CAST("{master_col}" AS TEXT) IN ({holders})')
    links=[]
    with core.engine.connect() as c:
        for r in c.execute(q,params).mappings():
            d=dict(r); tag=f"{_norm(d.get('source_type'))} {_norm(d.get('source_table'))}".upper()
            if ("WHATSAPP" in tag or "WA_PROPERTIES" in tag) and _norm(d.get("source_pk")): links.append(d)
    if not links:return {}
    pc=_cols(eng,"wa_properties")
    pid=_first(pc,"wa_property_id","id","property_id")
    sender=_first(pc,"sender_phone","phone_number","sender_number")
    broker=_first(pc,"broker_phone")
    owner=_first(pc,"owner_phone")
    if not pid or not sender:return {}
    wanted=list({_norm(x["source_pk"]) for x in links})
    pmap={}
    B=500
    with eng.connect() as c:
        for i in range(0,len(wanted),B):
            chunk=wanted[i:i+B]; ps={f"x{j}":v for j,v in enumerate(chunk)}
            holders=",".join(":"+k for k in ps)
            sel=f'"{pid}" AS source_pk,"{sender}" AS sender_phone'
            if broker: sel+=f',"{broker}" AS broker_phone'
            if owner: sel+=f',"{owner}" AS owner_phone'
            qq=text(f'SELECT {sel} FROM "wa_properties" WHERE CAST("{pid}" AS TEXT) IN ({holders})')
            for r in c.execute(qq,ps).mappings(): pmap[str(r["source_pk"])]=dict(r)
    out={}
    for l in links:
        d=pmap.get(_norm(l["source_pk"]))
        if not d: continue
        mid=str(l["master_id"])
        out.setdefault(mid,[])
        entry={"label":"WhatsApp Sender","value":_phone(d.get("sender_phone"))}
        if entry["value"] and entry["value"] not in [x["value"] for x in out[mid]]: out[mid].append(entry)
        for label,key in (("Explicit Broker", "broker_phone"),("Explicit Owner","owner_phone")):
            val=_phone(d.get(key))
            if val and val not in [x["value"] for x in out[mid]]: out[mid].append({"label":label,"value":val})
    return out

def _match(core,req):
    # Use the existing MASTER-ONLY matcher contract. Do not match directly against
    # pi_properties or pi_whatsapp_property_master.
    import alliance_master_matcher_contract_v1 as master_matcher
    raw=_norm(req.get("original_message"))
    if not raw: raise RuntimeError("Requirement has no original message")
    from alliance_astra_match_intelligence_v1 import interpret_requirement
    astra=interpret_requirement(req)
    enriched=astra.get("enriched_text") or raw
    result=master_matcher.run_match(core.engine,enriched,min_score=70.0,limit=100)
    result["astra_interpretation"]=astra
    items=[]
    for key,label in (("exact_verified","EXACT VERIFIED"),("exact_needs_verification","EXACT NEEDS VERIFICATION"),("alternatives","ALTERNATIVE")):
        for item in result.get(key) or []:
            d=dict(item); d["_bucket"]=label; items.append(d)
    ids=[str(d.get("record_id") or d.get("property_id") or d.get("canonical_id") or d.get("id") or "") for d in items]
    return result,items,_master_contact_map(core,ids),_master_whatsapp_sender_map(core,ids)

def _match_detail_map(core, ids):
    ids=[str(x) for x in ids if str(x or "").strip()]
    if not ids or not _table_exists(core.engine,MASTER_PROPERTY_TABLE): return {}
    params={f"p{i}":v for i,v in enumerate(ids)}
    holders=",".join(":"+k for k in params)
    q=text(f"""SELECT canonical_id,locality,city,transaction_type,area_sqft,price_raw,
                     clean_record,phones,created_at,updated_at
              FROM {MASTER_PROPERTY_TABLE}
              WHERE CAST(canonical_id AS TEXT) IN ({holders})""")
    out={}
    with core.engine.connect() as conn:
        for r in conn.execute(q,params).mappings():
            d=dict(r); cr=d.get("clean_record")
            if isinstance(cr,str):
                try: cr=json.loads(cr)
                except Exception: cr={}
            if not isinstance(cr,dict): cr={}
            def pick(*keys):
                for k in keys:
                    v=cr.get(k)
                    if v not in (None,"",[],{}): return v
                return ""
            desc=pick("team_description","description_edit","description","original_description","original_message","raw_line","source_text")
            area=pick("area_display","area","available_area") or d.get("area_sqft") or ""
            ptype=pick("property_type","asset_type","subtype")
            amount=pick("rent","monthly_rent","rent_amount","rent_in_figures","sale_price","sale_amount","price","asking_price","amount","price_raw") or d.get("price_raw") or ""
            out[str(d.get("canonical_id"))]={"description":_norm(desc),"area":_norm(area),"property_type":_norm(ptype),"price":_norm(amount),"clean_record":cr}
    return out

def _render_match(core,req):
    result,items,enrich,wa_sender=_match(core,req)
    ids=[str(d.get("record_id") or d.get("property_id") or d.get("canonical_id") or d.get("id") or "") for d in items[:100]]
    details=_match_detail_map(core,ids)
    rows=[]
    for d in items[:100]:
        pid=str(d.get("record_id") or d.get("property_id") or d.get("canonical_id") or d.get("id") or "")
        md=details.get(pid) or {}
        contacts=list((enrich.get(pid) or {}).get("contacts") or [])
        for x in wa_sender.get(pid) or []:
            if x.get("value") and x["value"] not in [z.get("value") for z in contacts]: contacts.append(x)
        contact_text=", ".join(x.get("value") for x in contacts if x.get("value")) or "Not captured"
        source=_norm(d.get("source_name") or d.get("source_bucket") or d.get("source") or ", ".join((enrich.get(pid) or {}).get("sources") or [])) or "Master"
        ptype=_norm(d.get("subtype") or md.get("property_type") or d.get("family") or "Not captured")
        loc=_norm(d.get("location") or "Not captured")
        tx=_norm(d.get("transaction") or "Not captured")
        area=_norm(d.get("area_display") or d.get("area_sqft") or d.get("area") or md.get("area") or "Not captured")
        price=_norm(d.get("price_display") or d.get("price") or md.get("price") or "Not captured")
        desc=_norm(md.get("description") or d.get("property") or "Not captured")
        verification=_norm(d.get("availability_verification") or d.get("verification") or "UNVERIFIED")
        why=d.get("why"); why=", ".join(str(x) for x in why) if isinstance(why,list) else _norm(why)
        draft=("Hi, we have shortlisted an option matching your requirement.\\n\\n"
               +"Location: "+loc+"\\nProperty Type: "+ptype+"\\nTransaction: "+tx
               +"\\nArea: "+area+"\\nPrice: "+price
               +"\\n\\nPlease let me know if you would like full details or a site visit.")
        verify_form=(f"<details class='inline'><summary class='btn good'>Verify / Remarks</summary>"
          f"<form method='post' action='/alliance/master-requirement-matcher/property/{quote(pid)}/verify'>"
          f"<input type='hidden' name='requirement_id' value='{_e(req.get('id'))}'>"
          "<select name='status' required><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>CALL_BACK</option><option>SOLD</option><option>RENTED</option><option>HOLD</option><option>WRONG_NUMBER</option></select>"
          "<select name='verified_with' required><option>OWNER</option><option>BROKER</option><option>OTHER</option></select>"
          "<input name='remarks' placeholder='Remarks'><input type='datetime-local' name='next_verification_at'>"
          "<button class='btn good' type='submit'>Save Verification</button></form></details>")
        edit=f"<a class='btn light' href='/alliance/primary/property/{quote(pid)}/edit'>Edit</a>"
        option=("Location: "+loc+" | Property Type: "+ptype+" | "+tx+" | Area: "+area+" | Price: "+price)
        msg=f"<button class='btn prepare' type='button' data-draft='{_e(draft)}' onclick='prepareMessage(this)'>Prepare Message</button>"
        rows.append("<tr>"
          f"<td><input class='pickmatch' type='checkbox' data-option='{_e(option)}' aria-label='Select property'> <b>Select</b><br>{_e(d.get('match_class') or d.get('_bucket'))}<br><small>Score {_e(d.get('match_score'))}</small></td>"
          f"<td>{_e(loc)}</td><td>{_e(ptype)}</td><td>{_e(area)}</td><td>{_e(tx)}</td><td>{_e(price)}</td>"
          f"<td class='desc'>{_e(desc)}</td><td><b>{_e(contact_text)}</b><br><small>Internal only</small></td>"
          f"<td>{_e(verification)}</td><td>{_e(source)}</td><td class='why'>{_e(why)}</td>"
          f"<td>{verify_form}{edit}{msg}<div class='draftbox' style='display:none'></div></td></tr>")
    summary=result.get("summary") or {}
    ai=result.get("astra_interpretation") or {}
    astra="<div class='summary'><b>Astra:</b> "+_e(ai.get("confidence") or "—")+" · "+_e(ai.get("transaction") or "—")+" · "+_e(", ".join(ai.get("locations") or []) or "—")+" · "+_e(ai.get("asset") or "—")+"</div>"
    counts="<div class='summary'><b>Master matches:</b> Verified "+_e(summary.get("exact_verified") or 0)+" · To verify "+_e(summary.get("exact_needs_verification") or 0)+" · Alternatives "+_e(summary.get("approved_alternatives") or 0)+"</div>"
    if not rows:
        return astra+counts+"<div class='summary'><b>No qualifying Master Property match found.</b></div>"
    headers=["Match","Location","Property Type","Area","Rent/Sale","Price","Property Details","Team Contact","Status","Source","Why matched","Team Action"]
    multi="<div class='messagebar'><button class='btn good' type='button' onclick='prepareSelectedMessage()'>Prepare 1 Message from Selected</button><span> Select one or more verified options.</span><div id='combinedDraft' class='draftbox' style='display:none'></div></div>"
    return astra+counts+multi+"<div class='matchtable'><table><thead><tr>"+''.join("<th>"+h+"</th>" for h in headers)+"</tr></thead><tbody>"+''.join(rows)+"</tbody></table></div>"

def _page(core,request:Request):
    rows=_apply_requirement_sender(_requirements(core))
    cat=_norm(request.query_params.get("category") or "ALL").upper()
    allowed={"ALL","MANUAL","WHATSAPP","NEWSPAPER","DISCOVERY","OTHER"}
    if cat not in allowed: cat="ALL"
    filtered=rows if cat=="ALL" else [r for r in rows if r["category"]==cat]
    counts={k:0 for k in allowed if k!="ALL"}
    for r in rows: counts[r["category"]]=counts.get(r["category"],0)+1
    tabs=" ".join(f"<a class='tab' href='{WORKSPACE_ROUTE}?category={c}'>{c} ({len(rows) if c=='ALL' else counts.get(c,0)})</a>" for c in ("ALL","MANUAL","WHATSAPP","NEWSPAPER","DISCOVERY","OTHER"))
    trs=[]
    for r in filtered[:2000]:
        rid=str(r.get("id")); loc=", ".join(str(x) for x in r.get("locations_list") or [])
        explicit=", ".join(str(x) for x in r.get("contacts_list") or []) or "—"
        sender=r.get("whatsapp_sender_phone") or "—"
        trs.append("<tr>"
          f"<td>{_e(rid)}</td><td>{_e(r['category'])}</td><td>{_e(r.get('source_type'))}</td><td>{_e(loc)}</td>"
          f"<td>{_e(r.get('property_category'))}</td><td>{_e(r.get('transaction_type'))}</td><td>{_e(r.get('budget_min'))} - {_e(r.get('budget_max'))}</td>"
          f"<td>{_e(explicit)}</td><td>{_e(sender)}</td><td>{_e(r.get('whatsapp_sender_name') or '—')}</td><td>{_e(r.get('whatsapp_sender_id') or '—')}</td>"
          f"<td>{_e(r.get('classification'))}</td><td>{_e(_norm(r.get('original_message'))[:260])}</td>"
          f"<td><a class='btn' href='{WORKSPACE_ROUTE}?category={cat}&requirement_id={quote(rid)}#results'>Run Matcher</a></td></tr>")
    rid=_norm(request.query_params.get("requirement_id")); results=""
    if rid:
        req=_find_req(rows,rid)
        if req:
            # A Run Matcher action is a focused action, not a navigation request.
            # Render only the selected requirement and its Master Property matches.
            sender_phone=req.get('whatsapp_sender_phone') or '—'
            sender_name=req.get('whatsapp_sender_name') or ''
            results="<div id='results'><h2>Master Database Matches</h2><div class='card'>"+ \
                f"<b>Requirement #{_e(rid)}</b><br>{_e(req.get('original_message'))}<br>" + \
                f"Requirement contact: {_e(', '.join(str(x) for x in req.get('contacts_list') or []) or '—')}<br>" + \
                f"<b>WhatsApp Sender:</b> {_e(sender_phone)} {_e(sender_name)}</div>" + \
                _render_match(core,req)+"</div>"
            return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Master Requirement Matcher</title>
<style>body{{font-family:Arial;margin:16px;background:#f6f8fb;color:#172437}}.btn{{display:inline-block;padding:7px 9px;margin:2px;border:0;border-radius:5px;background:#1769aa;color:white;text-decoration:none;cursor:pointer;font-size:11px}}.good{{background:#067647!important}}.light{{background:#475467!important}}.card,.summary,.messagebar{{background:white;border:1px solid #dfe5eb;border-radius:8px;padding:10px;margin:8px 0}}.matchtable{{overflow:auto;max-height:72vh;border:1px solid #dfe5eb;background:white}}.matchtable table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}.matchtable th,.matchtable td{{border:1px solid #dfe5eb;padding:6px;vertical-align:top;text-align:left;max-width:240px}}.matchtable th{{position:sticky;top:0;background:#eef3f8;z-index:3;white-space:nowrap}}.desc{{min-width:260px;max-width:380px!important}}.why{{max-width:220px!important}}details.inline form{{min-width:230px;background:#fff;padding:6px;border:1px solid #ccd5df}}details.inline input,details.inline select{{width:100%;margin:2px 0;padding:5px}}.draftbox{{white-space:pre-wrap;background:#f7fafc;border:1px solid #dfe5eb;border-radius:6px;padding:8px;margin-top:6px;min-width:250px}}.pickmatch{{width:18px;height:18px;vertical-align:middle}}</style><script>
function showDraft(box,draft){{box.style.display='block';box.textContent='';var title=document.createElement('b');title.textContent='Client Message Draft — verify facts before sending';box.appendChild(title);box.appendChild(document.createElement('br'));box.appendChild(document.createElement('br'));var txt=document.createElement('span');txt.className='drafttext';txt.textContent=draft;box.appendChild(txt);box.appendChild(document.createElement('br'));box.appendChild(document.createElement('br'));var cp=document.createElement('button');cp.className='btn';cp.type='button';cp.textContent='Copy Message';cp.onclick=function(){{navigator.clipboard.writeText(draft)}};box.appendChild(cp)}}
function prepareMessage(b){{var cell=b.closest('td');var box=cell?cell.querySelector('.draftbox'):null;if(!box)return;showDraft(box,b.getAttribute('data-draft')||'')}}
function prepareSelectedMessage(){{var a=[...document.querySelectorAll('.pickmatch:checked')];var box=document.getElementById('combinedDraft');if(!a.length){{box.style.display='block';box.textContent='Select at least one property first.';return}}var lines=['Hi, we have shortlisted the following options matching your requirement:',''];a.forEach(function(x,i){{lines.push((i+1)+'. '+(x.getAttribute('data-option')||''))}});lines.push('','Please let me know which option you would like full details or a site visit for.');showDraft(box,lines.join('\\n'))}}
</script></head><body>
<p><a href='javascript:history.back()'>← Previous Page</a> · <a href='/alliance/primary'>Dashboard</a></p>
<h1>Alliance Master Requirement Matcher</h1>
<div class='card'>Requirement authority: <b>{MASTER_REQUIREMENT_TABLE}</b> · Property authority: <b>{MASTER_PROPERTY_TABLE}</b> · Matcher source: <b>MASTER ONLY</b>.</div>
{results}</body></html>"""
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Master Requirement Matcher</title>
<style>body{{font-family:Arial;margin:22px;background:#f6f8fb;color:#172437}}table{{width:100%;border-collapse:collapse;background:white;font-size:12px}}th,td{{border-bottom:1px solid #e1e7ee;padding:7px;vertical-align:top;text-align:left}}th{{background:#eef3f8}}.tab,.btn{{display:inline-block;padding:8px 10px;margin:3px;border-radius:7px;background:#1769aa;color:white;text-decoration:none}}.card{{background:white;border:1px solid #dfe5eb;border-radius:10px;padding:12px;margin:10px 0}}.contacts{{margin:8px 0;padding:8px;background:#f7fafc}}</style></head><body>
<p><a href='/alliance/primary'>← Dashboard</a></p><h1>Alliance Master Requirement Matcher</h1>
<div class='card'>Requirement authority: <b>{MASTER_REQUIREMENT_TABLE}</b> · Property authority: <b>{MASTER_PROPERTY_TABLE}</b> · WhatsApp sender provenance: <b>LIVE FALLBACK</b> · Contacts: <b>authenticated staff only</b>.</div>
<div>{tabs}</div><h3>{_e(cat)} requirements: {_e(len(filtered))}</h3>
<table><tr><th>ID</th><th>Category</th><th>Source</th><th>Location</th><th>Asset</th><th>Transaction</th><th>Budget</th><th>Contact</th><th>WhatsApp Sender Phone</th><th>Sender Name</th><th>Sender ID (not phone)</th><th>Status</th><th>Original Requirement</th><th>Action</th></tr>{''.join(trs)}</table>
{results}</body></html>"""

def _status(core):
    rows=_apply_requirement_sender(_requirements(core))
    counts={}; wa=with_sender=with_contact=0
    for r in rows:
        counts[r["category"]]=counts.get(r["category"],0)+1
        if r["category"]=="WHATSAPP":
            wa+=1
            if r.get("whatsapp_sender_phone"): with_sender+=1
            if r.get("contacts_list"): with_contact+=1
    return {"status":"PASS","version":VERSION,"master_requirement_table":MASTER_REQUIREMENT_TABLE,
            "active_requirements":len(rows),"categories":counts,"whatsapp_requirements":wa,
            "whatsapp_sender_resolved":with_sender,"whatsapp_contact_available":with_contact,
            "master_property_table":MASTER_PROPERTY_TABLE,"matcher_source_contract":"MASTER_ONLY",
            "contacts_scope":"AUTHENTICATED_STAFF_ONLY","sender_policy":"ALWAYS_SHOW_WHATSAPP_SENDER_WHEN_SOURCE_PROVENANCE_EXISTS"}

_IDENTITY_REGISTRY_STATE={"status":"IDLE","version":"1.0.0-DETERMINISTIC-LID-PHONE-REGISTRY"}

def _start_identity_registry_refresh():
    if _IDENTITY_REGISTRY_STATE.get("status") in ("RUNNING","PASS"):
        return _IDENTITY_REGISTRY_STATE
    try:
        import threading
        from alliance_whatsapp_sender_identity_registry_v1 import apply_registry
        eng=_wa_engine()
        if eng is None:
            _IDENTITY_REGISTRY_STATE.update({"status":"SKIPPED","reason":"WA_ENGINE_UNAVAILABLE"})
            return _IDENTITY_REGISTRY_STATE
        _IDENTITY_REGISTRY_STATE.update({"status":"RUNNING"})
        def worker():
            try:
                rep=apply_registry(eng)
                try:
                    from alliance_whatsapp_identity_bridge_v1 import run as run_identity_bridge
                    bridge_rep=run_identity_bridge(eng)
                except Exception as bridge_error:
                    bridge_rep={"status":"ERROR","error":type(bridge_error).__name__+": "+str(bridge_error)[:300]}
                _IDENTITY_REGISTRY_STATE.update({
                    "status":"PASS" if rep.get("rows_scanned",0)>0 else ("ERROR" if rep.get("loader_error") else "NO_ROWS_SCANNED"),
                    "tables_scanned":rep.get("tables_scanned",0),
                    "tables_discovered":rep.get("tables_discovered",[]),
                    "rows_scanned":rep.get("rows_scanned",0),
                    "wa_rows":rep.get("wa_rows",0),
                    "wai_rows":rep.get("wai_rows",0),
                    "exact_pairs":rep.get("exact_pairs",0),
                    "candidate_pairs":rep.get("candidate_pairs",0),
                    "resolved_unique":rep.get("resolved_unique",0),
                    "ambiguous":rep.get("ambiguous",0),
                    "loader_error":rep.get("loader_error"),
                    "upstream_identity_bridge":bridge_rep,
                })
            except Exception as e:
                _IDENTITY_REGISTRY_STATE.update({"status":"ERROR","error":type(e).__name__+": "+str(e)[:240]})
        threading.Thread(target=worker,name="alliance-wa-sender-identity-registry",daemon=True).start()
    except Exception as e:
        _IDENTITY_REGISTRY_STATE.update({"status":"ERROR","error":type(e).__name__+": "+str(e)[:240]})
    return _IDENTITY_REGISTRY_STATE

def register(core, served_app=None):
    app=served_app or _app(core)
    for route in list(getattr(app.router,"routes",[])):
        if getattr(route,"path",None)==SMART_MATCHER_ROUTE and "GET" in (getattr(route,"methods",set()) or set()):
            app.router.routes.remove(route)
        if getattr(route,"path",None) in (WORKSPACE_ROUTE,"/api/alliance/master-requirements-v1/status"):
            app.router.routes.remove(route)
    @app.get(SMART_MATCHER_ROUTE)
    async def smart_matcher_redirect(request:Request):
        _auth(core,request)
        # Preserve the selected requirement across the canonical matcher takeover.
        # Previously the redirect discarded requirement_id and opened the ALL list.
        qs=str(request.url.query or "").strip()
        target=WORKSPACE_ROUTE + (("?" + qs) if qs else "")
        return RedirectResponse(target,status_code=302)
    @app.get(WORKSPACE_ROUTE,response_class=HTMLResponse)
    async def master_requirement_page(request:Request):
        _auth(core,request); return HTMLResponse(_page(core,request))
    @app.post("/alliance/master-requirement-matcher/property/{cid}/verify")
    async def matcher_property_verify(
        cid:str, request:Request,
        requirement_id:str=Form(""),
        status:str=Form("AVAILABLE"),
        verified_with:str=Form("OTHER"),
        remarks:str=Form(""),
        next_verification_at:str=Form(""),
    ):
        _auth(core,request)
        status=_norm(status).upper()
        allowed={"AVAILABLE","NOT_AVAILABLE","CALL_BACK","SOLD","RENTED","HOLD","WRONG_NUMBER"}
        if status not in allowed: raise HTTPException(400,"Invalid verification status")
        actor_fn=getattr(core,"actor_name",None)
        actor=actor_fn(request) if callable(actor_fn) else "team"
        available="AVAILABLE" if status=="AVAILABLE" else ("UNAVAILABLE" if status in {"NOT_AVAILABLE","SOLD","RENTED","WRONG_NUMBER"} else "UNKNOWN")
        verification="VERIFIED" if status=="AVAILABLE" else "UNVERIFIED"
        with core.engine.begin() as conn:
            conn.execute(text("""INSERT INTO pi_master_workflow_v720(canonical_id,entity_type,verification_status,verified_at,verified_by,availability_status,internal_notes,updated_at)
                VALUES(:id,'PROPERTY',:vs,NOW(),:by,:av,:notes,NOW())
                ON CONFLICT(canonical_id) DO UPDATE SET verification_status=:vs,verified_at=NOW(),verified_by=:by,
                availability_status=:av,internal_notes=:notes,updated_at=NOW()"""),
                {"id":cid,"vs":verification,"by":actor,"av":available,"notes":_norm(remarks) or None})
            try:
                conn.execute(text("""INSERT INTO pi_master_action_log_v730(canonical_id,entity_type,action,actor,details)
                    VALUES(:id,'PROPERTY','MATCHER_VERIFICATION',:by,CAST(:d AS JSONB))"""),
                    {"id":cid,"by":actor,"d":json.dumps({"status":status,"verified_with":verified_with,"remarks":remarks,"next_verification_at":next_verification_at},ensure_ascii=False)})
            except Exception:
                pass
        rid=_norm(requirement_id)
        target=WORKSPACE_ROUTE+(("?requirement_id="+quote(rid)+"#results") if rid else "")
        return RedirectResponse(target,status_code=303)

    @app.get("/api/alliance/master-requirements-v1/status")
    async def master_requirement_status(request:Request):
        _auth(core,request); return JSONResponse(_status(core))
    return {"status":"REGISTERED","version":VERSION,"requirement_authority":MASTER_REQUIREMENT_TABLE,
            "property_authority":MASTER_PROPERTY_TABLE,"matcher_source_contract":"MASTER_ONLY",
            "smart_matcher_takeover":True,"whatsapp_sender_fallback":True,
            "sender_identity_registry":_start_identity_registry_refresh(),
            "contacts_scope":"AUTHENTICATED_STAFF_ONLY",
            "routes":[WORKSPACE_ROUTE,"/api/alliance/master-requirements-v1/status",SMART_MATCHER_ROUTE]}
