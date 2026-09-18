from __future__ import annotations

import html, json, re
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="1.8.0-WHATSAPP-OPAQUE-ID-REGISTRY-RESOLUTION"
PHONE_RE=re.compile(r"(?<!\d)(?:\+?91[\s.\-]?)?([6-9](?:[\s.\-]?\d){9})(?!\d)")
SOURCES=("MASTER","NEWSPAPER","MANUAL","MAGAZINE","WHATSAPP")

def _e(v):return html.escape("" if v is None else str(v))
def _dict(v):
    if isinstance(v,dict):return dict(v)
    if isinstance(v,str):
        try:x=json.loads(v);return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}
def _phones(*values):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            for x in v.values():walk(x)
        elif isinstance(v,(list,tuple,set)):
            for x in v:walk(x)
        else:
            s=str(v or "").replace("@s.whatsapp.net","").replace("@c.us","")
            for m in PHONE_RE.finditer(s):
                p=re.sub(r"\D","",m.group(1))
                if len(p)==10 and p not in out:out.append(p)
    for v in values:walk(v)
    return ", ".join(out)
def _first(d,keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"",[],{}):return v
    return ""
def _shown(v):return "Not captured" if v in (None,"",[],{}) else str(v)
def _classify_source(v):
    s=str(v or "").upper()
    if "NEWSPAPER" in s:return "NEWSPAPER"
    if "MANUAL" in s or s in {"PI_REQUIREMENTS","PI_OPERATIONAL_REQUIREMENTS","PI_RETAIL_REQUIREMENTS","PI_HOSPITALITY_REQUIREMENTS"}:return "MANUAL"
    if "MAGAZINE" in s:return "MAGAZINE"
    if "WHATSAPP" in s or s.startswith("WA_") or "WAI_" in s:return "WHATSAPP"
    return "OTHER"
def _nav():return "<div class='nav'><a class='btn' href='#' onclick='history.back();return false'>← Previous Page</a><a class='btn' href='/alliance/primary'>Dashboard</a></div>"
def _page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#172033;font:13px Arial}}header{{background:#10223f;color:white;padding:12px 16px}}.wrap{{max-width:1920px;margin:auto;padding:10px}}.nav{{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}}a.btn,button{{display:inline-block;background:#10223f;color:white;text-decoration:none;padding:7px 9px;border:0;border-radius:5px;font-weight:800;font-size:11px}}.grid{{display:grid;grid-template-columns:repeat(5,minmax(145px,1fr));gap:7px}}.card{{background:white;border:1.6px solid #7d8998;border-radius:8px;padding:9px;margin-bottom:8px}}.tablebox{{overflow:auto;max-height:78vh;background:white;border:2px solid #475467;border-radius:6px}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px;font-weight:650}}th,td{{border:1.5px solid #667085;padding:5px 6px;text-align:left;vertical-align:top;line-height:1.25}}th{{position:sticky;top:0;background:#cfdceb;color:#0b1f3a;font-weight:900;z-index:3}}tr:nth-child(even){{background:#f3f6fa}}.desc{{min-width:260px;max-width:430px;white-space:pre-wrap}}.loc{{min-width:90px;max-width:160px;white-space:normal}}.date{{min-width:110px;max-width:145px}}input{{padding:7px;border:1.4px solid #667085;border-radius:5px;min-width:180px}}</style></head><body><header><b>Alliance CRE Operating System</b></header><div class='wrap'>{_nav()}<h2>{_e(title)}</h2>{body}</div></body></html>"""
def _property_hub():
    cards=[("Master Properties","/alliance/final/database/master"),("Newspaper","/alliance/final/database/newspaper"),("Magazine","/alliance/final/database/magazine"),("WhatsApp","/alliance/final/database/whatsapp"),("Manual","/alliance/final/database/manual")]
    return _page("Property Databases","<div class='grid'>"+"".join(f"<div class='card'><h3>{t}</h3><a class='btn' href='{u}'>Open</a></div>" for t,u in cards)+"</div>")
def _master_properties(engine,q=""):
    return _page("Master Properties","<div class=card><a class=btn href='/alliance/primary/properties'>Open canonical property database</a></div>")
def _wa_lookup(row):
    return _phones(row)
def _norm_req(obj,source_table=""):
    d=dict(obj or {});ex=_dict(d.get("extracted_fields"));cr=_dict(d.get("clean_record"));m={};m.update(ex);m.update(cr);m.update({k:v for k,v in d.items() if v not in (None,"",[],{})});src=_classify_source(d.get("source_type") or d.get("source") or source_table);loc=_first(m,["locations","preferred_locations","preferred_location","location","locality","city","area_name","micro_market"])
    if isinstance(loc,list):loc=", ".join(str(x) for x in loc if x)
    single=_first(m,["area_sqft","requirement_sqft","required_area","area"]);amin=_first(m,["area_min_sqft","minimum_area_sqft","minimum_area","min_area_sqft","min_area"]) or single;amax=_first(m,["area_max_sqft","maximum_area_sqft","maximum_area","max_area_sqft","max_area"]) or single
    return {"date":_first(m,["created_at","message_timestamp","timestamp","date","captured_at"]),"description":_first(m,["original_message","requirement_message","raw_text","message","requirement_text","requirement","description","additional_points","remarks","notes","content"]),"company":_first(m,["company_name","brand_name","client_company","company","retailer_name","company_brand_person"]),"name":_first(m,["contact_name","client_name","sender_name","name"]),"contact":_phones(m) or "Not captured","location":loc or "Not captured","category":_first(m,["intended_use","suitable_category","category","property_category","business_category"]),"ptype":_first(m,["property_type","required_property_type","asset_type"]),"area_min":amin,"area_max":amax,"transaction":_first(m,["transaction_type","transaction","rent_sale","rent_or_sale","deal_type"]),"budget":_first(m,["budget_max","budget","sale_budget","rent_budget","budget_raw","budget_max_inr"]),"floor":_first(m,["floor","preferred_floor","floor_preference","entry_status","additional_preferences"]),"verification":_first(m,["verification_status","classification","status"]) or "UNVERIFIED","source":src if src!='OTHER' else source_table,"source_id":_first(m,["source_pk","wa_requirement_id","requirement_id","record_id","id"]),"canonical_id":_first(m,["canonical_id","master_requirement_id","master_id"]),"gate_id":_first(m,["gate_id","id"]) if source_table=="pi_requirement_gate_v1191" else ""}
def _requirement_rows(engine,source,limit=500):
    # FAST PATH ONLY: canonical gate + canonical master. Do not inspect every requirement
    # table on every HTTP request; that was causing multi-thousand-row scans and hangs.
    rows=[];seen=set()
    def add(raw,table):
        r=_norm_req(_dict(raw),table);src=r['source']
        if source!='MASTER' and src!=source:return
        seed='|'.join(str(r.get(k) or '').lower() for k in ('description','contact','location','company','source_id'))
        if seed in seen:return
        seen.add(seed);rows.append(r)
    try:
        with engine.connect() as c:
            data=c.execute(text("SELECT to_jsonb(g) FROM pi_requirement_gate_v1191 g WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED') ORDER BY created_at DESC NULLS LAST,id DESC LIMIT :n"),{'n':limit*2}).scalars().all()
        for x in data:add(x,'pi_requirement_gate_v1191')
    except Exception:pass
    if source=='MASTER':
        try:
            with engine.connect() as c:data=c.execute(text("SELECT to_jsonb(r) FROM pi_master_requirements_v711 r ORDER BY created_at DESC NULLS LAST LIMIT :n"),{'n':limit}).scalars().all()
            for x in data:add(x,'pi_master_requirements_v711')
        except Exception:pass
    # Use the same restored WhatsApp requirement contact authority used by
    # WhatsApp recovery/availability. Bulk lookup avoids one DB query per row.
    missing=[r for r in rows if r.get('source')=='WHATSAPP' and r.get('contact') in ('','Not captured') and str(r.get('source_id') or '').startswith('WAR-')]
    if missing:
        try:
            import whatsapp_live_bridge as wb
            ids=list(dict.fromkeys(str(r['source_id']) for r in missing))[:1000]
            params={f'id{i}':v for i,v in enumerate(ids)}
            marks=','.join(':'+k for k in params)
            with wb.wa_engine.connect() as wc:
                restored=wc.execute(text(f"""SELECT r.wa_requirement_id,r.contact_phone,
                    e.sender_name,e.sender_phone
                    FROM wa_requirements r
                    LEFT JOIN wa_bridge_events e ON e.entity_id=r.wa_requirement_id
                    WHERE r.wa_requirement_id IN ({marks})"""),params).mappings().all()
                phone_map={}
                unresolved=[]
                for x in restored:
                    p=_phones(x.get('contact_phone'))
                    if not p:p=_phones(x.get('sender_phone'))
                    if p:phone_map[str(x['wa_requirement_id'])]=p
                    else:unresolved.append(x)

                # First resolve WhatsApp opaque/LID identities through the dedicated
                # evidence registry populated by live identity capture. This is the
                # authoritative LID -> real phone mapping and never uses account_phone.
                opaque_ids=[]
                for x in unresolved:
                    raw=str(x.get('sender_phone') or '').strip()
                    digits=''.join(ch for ch in raw.split('@',1)[0] if ch.isdigit())
                    if len(digits)>=13:opaque_ids.append(digits)
                opaque_ids=list(dict.fromkeys(opaque_ids))[:500]
                registry_map={}
                if opaque_ids:
                    op={f'o{i}':v for i,v in enumerate(opaque_ids)}
                    om=','.join(':'+k for k in op)
                    try:
                        regs=wc.execute(text(f"""SELECT opaque_id,resolved_phone
                            FROM pi_whatsapp_sender_identity_registry_v1
                            WHERE opaque_id IN ({om})
                              AND resolution_status='RESOLVED_UNIQUE_EXACT_EVIDENCE'
                              AND confidence>=100 AND conflicting_phones=0
                              AND resolved_phone IS NOT NULL"""),op).mappings().all()
                        registry_map={str(z.get('opaque_id') or ''):_phones(z.get('resolved_phone')) for z in regs}
                    except Exception:
                        registry_map={}
                still=[]
                for x in unresolved:
                    raw=str(x.get('sender_phone') or '').strip()
                    oid=''.join(ch for ch in raw.split('@',1)[0] if ch.isdigit())
                    p=registry_map.get(oid)
                    if p:phone_map[str(x['wa_requirement_id'])]=p
                    else:still.append(x)
                unresolved=still

                # Secondary evidence: historical events for the same sender name.
                # Accept only one unique real phone for that sender.
                names=list(dict.fromkeys(str(x.get('sender_name') or '').strip() for x in unresolved if str(x.get('sender_name') or '').strip()))[:500]
                sender_map={}
                if names:
                    np={f'n{i}':v for i,v in enumerate(names)}
                    nm=','.join(':'+k for k in np)
                    hist=wc.execute(text(f"""SELECT sender_name,sender_phone
                        FROM wa_bridge_events
                        WHERE sender_name IN ({nm})
                          AND sender_phone IS NOT NULL AND BTRIM(sender_phone)<>''"""),np).mappings().all()
                    candidates={}
                    for h in hist:
                        p=_phones(h.get('sender_phone'))
                        if p:candidates.setdefault(str(h.get('sender_name') or '').strip(),set()).add(p)
                    sender_map={n:next(iter(ps)) for n,ps in candidates.items() if len(ps)==1}
                for x in unresolved:
                    p=sender_map.get(str(x.get('sender_name') or '').strip())
                    if p:phone_map[str(x['wa_requirement_id'])]=p
            for r in missing:
                p=phone_map.get(str(r.get('source_id') or ''))
                if p:r['contact']=p
        except Exception:pass
    rows.sort(key=lambda r:str(r.get('date') or ''),reverse=True);return rows[:limit]
def _action(r,source):
    cid=str(r.get('canonical_id') or '').strip();sid=str(r.get('source_id') or '').strip();src=str(r.get('source') or source).upper();gid=str(r.get('gate_id') or '').strip();bits=[]
    if cid:bits.append(f"<a class='btn' href='/alliance/primary/matcher?requirement_id={_e(cid)}'>Run Matcher</a>")
    elif gid:bits.append(f"<a class='btn' href='/alliance/final/requirements/run-match?gate_id={_e(gid)}'>Run Matcher</a>")
    elif sid:bits.append(f"<a class='btn' href='/alliance/final/requirements/run-match?source={_e(src)}&source_pk={_e(sid)}'>Run Matcher</a>")
    return ' '.join(bits) or 'Review'
def _requirements_page(engine,source,q=""):
    rows=_requirement_rows(engine,source,500);q=str(q or '').strip().lower()
    if q:rows=[r for r in rows if q in ' '.join(str(v or '') for v in r.values()).lower()]
    trs=[]
    for r in rows:
        vals=[r['date'],r['description'],r['company'],r['name'],r['contact'],r['location'],r['category'],r['ptype'],r['area_min'],r['area_max'],r['transaction'],r['budget'],r['floor'],r['verification'],r['source'],r['source_id']];cells=[f"<td class='{('date' if i==0 else 'desc' if i==1 else 'loc' if i==5 else '')}'>{_e(_shown(v))}</td>" for i,v in enumerate(vals)];cells.append(f"<td>{_action(r,source)}</td>");trs.append('<tr>'+''.join(cells)+'</tr>')
    heads=['Date / Time','Requirement / Description','Client / Company','Contact Name','Contact No.','Location','Category / Purpose','Property Type','Area Min','Area Max','Rent / Sale','Budget','Floor / Preference','Verification','Source','Source ID','Action']
    return _page('Master Requirements' if source=='MASTER' else source.title()+' Requirements',f"<div class=card><form><input name=q value='{_e(q)}' placeholder='Search requirements'> <button>Search</button></form><b>{len(rows)}</b> latest requirements shown.</div><div class=tablebox><table><thead><tr>{''.join('<th>'+h+'</th>' for h in heads)}</tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan=17>No requirements found.</td></tr>'}</tbody></table></div>")
def _requirement_hub():
    cards=[('Master Requirements','master'),('WhatsApp','whatsapp'),('Newspaper','newspaper'),('Magazine','magazine'),('Manual','manual')];return _page('Requirement Databases',"<div class=grid>"+''.join(f"<div class=card><h3>{t}</h3><a class=btn href='/alliance/final/requirements/{s}'>Open</a></div>" for t,s in cards)+'</div>')
def _manual_page():return _page('Manual',"<div class=card><a class=btn href=/alliance/final/requirements/add-manual>+ Add Requirement</a></div>")
def _whatsapp_requirements():return RedirectResponse('/alliance/final/requirements/whatsapp',307)
def register(core,requirement_app=None,served_app=None):
    app=served_app or getattr(core,'app',None) or core;engine=getattr(core,'engine',None)
    @app.middleware('http')
    async def clean_database_pages(request:Request,call_next):
        path=request.url.path.rstrip('/') or '/'
        if path=='/alliance/source/newspaper':return RedirectResponse('/newspaper-v83',307)
        if path=='/alliance/source/manual':return HTMLResponse(_manual_page(),headers={'Cache-Control':'no-store'})
        if path=='/alliance/final/databases':return HTMLResponse(_property_hub(),headers={'Cache-Control':'no-store'})
        # Property database authority: render every source through the canonical V9.3
        # database renderer. The unified patch supplies the approved 19-column layout,
        # zoom controls and reads normalized p.phones for WhatsApp contacts. Do not
        # replace Master with the old one-link placeholder page.
        if path.startswith('/alliance/final/database/') and engine is not None:
            src=path.rsplit('/',1)[-1].upper()
            if src in SOURCES:
                import alliance_final_5x5_databases_v910 as property_db
                import alliance_property_database_unified_patch_v1 as property_patch
                property_patch.install()
                qp=request.query_params
                try: limit=max(1,min(1500,int(qp.get('limit','500') or 500)))
                except Exception: limit=500
                body=property_db._property_table(core,engine,request,src,qp.get('q',''),qp.get('location',''),qp.get('category',''),qp.get('transaction',''),qp.get('status',''),qp.get('assigned',''),limit)
                page=property_db._shell(f"{src.title()} Property Database",body)
                return HTMLResponse(page,headers={'Cache-Control':'no-store'})
        if path=='/alliance/final/requirements':return HTMLResponse(_requirement_hub(),headers={'Cache-Control':'no-store'})
        if path.startswith('/alliance/final/requirements/') and engine is not None:
            src=path.rsplit('/',1)[-1].upper()
            if src in SOURCES:return HTMLResponse(_requirements_page(engine,src,request.query_params.get('q','')),headers={'Cache-Control':'no-store'})
        if path=='/whatsapp-live/requirements':return _whatsapp_requirements()
        return await call_next(request)
    return {'status':'REGISTERED','version':VERSION,'requirement_query':'CANONICAL_FAST_PATH_MAX_500','schema_scan_per_request':False,'database_changed':False}
