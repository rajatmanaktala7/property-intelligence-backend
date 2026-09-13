from __future__ import annotations
import html, json, re
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import inspect, text

VERSION="1.1.0-MASTER-REQUIREMENT-AUTHORITY-SAFE"
MASTER_REQUIREMENT_TABLE="pi_requirement_gate_v1191"
MASTER_PROPERTY_TABLE="pi_master_properties_v711"
WORKSPACE_ROUTE="/alliance/master-requirement-matcher"
SMART_MATCHER_ROUTE="/alliance/primary/matcher"

def _app(core): return getattr(core,"app",None) or core

def _auth(core,request:Request):
    try:
        core.need_login(request)
    except Exception as exc:
        raise HTTPException(status_code=401,detail="Login required") from exc

def _e(v): return html.escape(str(v or ""))

def _norm(v):
    return re.sub(r"\s+"," ",str(v or "").replace("\u00a0"," ")).strip()

def _json_list(v):
    if isinstance(v,list): return v
    if v in (None,""): return []
    if isinstance(v,str):
        try:
            x=json.loads(v)
            return x if isinstance(x,list) else [x]
        except Exception:
            return [v]
    return [v]

def _category(source_type,source_table):
    s=f"{_norm(source_type)} {_norm(source_table)}".upper()
    if "WHATSAPP" in s or re.search(r"\bWA_",s): return "WHATSAPP"
    if "NEWSPAPER" in s or "MAGAZINE" in s or "CAPTURE" in s: return "NEWSPAPER"
    if "DISCOVER" in s: return "DISCOVERY"
    if "MANUAL" in s or "FORM" in s: return "MANUAL"
    return "OTHER"

def _table_exists(engine,name):
    return name in set(inspect(engine).get_table_names())

def _requirements(core):
    if not _table_exists(core.engine,MASTER_REQUIREMENT_TABLE):
        raise RuntimeError(f"{MASTER_REQUIREMENT_TABLE} missing")
    sql=text(f"""
        SELECT
            id,evidence_key,source_type,source_table,source_pk,source_group,source_date,
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
            d=dict(r)
            d["category"]=_category(d.get("source_type"),d.get("source_table"))
            d["locations_list"]=_json_list(d.get("locations"))
            d["contacts_list"]=_json_list(d.get("contact_numbers"))
            out.append(d)
    return out

def _find_req(rows,rid):
    sr=str(rid)
    for r in rows:
        if str(r.get("id"))==sr: return r
    return None

def _cols(engine,table):
    return [c["name"] for c in inspect(engine).get_columns(table)]

def _first(cols,*names):
    low={c.lower():c for c in cols}
    for n in names:
        if n.lower() in low:return low[n.lower()]
    return None

def _master_contact_map(core,ids):
    ids=[str(x) for x in ids if str(x or "").strip()]
    if not ids or not _table_exists(core.engine,MASTER_PROPERTY_TABLE): return {}
    cols=_cols(core.engine,MASTER_PROPERTY_TABLE)
    idcol=_first(cols,"property_id","record_id","canonical_id","id")
    if not idcol:return {}
    contact_cols=[c for c in cols if c.lower() in {
        "contact_phone","contact_numbers","phone","mobile","owner_phone","broker_phone",
        "sender_phone","whatsapp_sender","contact_no","phone_number"
    }]
    source_cols=[c for c in cols if c.lower() in {"source","source_name","source_type","source_group"}]
    if not contact_cols and not source_cols:return {}
    select=[f'"{idcol}" AS pid']
    select += [f'"{c}" AS "{c}"' for c in contact_cols+source_cols]
    params={f"p{i}":v for i,v in enumerate(ids)}
    holders=",".join(":"+k for k in params)
    q=text(f'SELECT {",".join(select)} FROM "{MASTER_PROPERTY_TABLE}" WHERE CAST("{idcol}" AS TEXT) IN ({holders})')
    out={}
    with core.engine.connect() as c:
        for r in c.execute(q,params).mappings():
            d=dict(r)
            contacts=[]
            for col in contact_cols:
                vals=_json_list(d.get(col))
                for v in vals:
                    sv=_norm(v)
                    if sv and sv not in [x["value"] for x in contacts]:
                        contacts.append({"label":col,"value":sv})
            sources=[_norm(d.get(col)) for col in source_cols if _norm(d.get(col))]
            out[str(d.get("pid"))]={"contacts":contacts,"sources":sources}
    return out

def _match(core,req):
    import alliance_phase5_canonical_matcher as phase5
    raw=_norm(req.get("original_message"))
    if not raw: raise RuntimeError("Requirement has no original message")
    result=phase5.run_match(core.engine,raw,min_score=70.0,limit=100)
    buckets=[]
    for key,label in (
        ("exact_verified","EXACT VERIFIED"),
        ("exact_needs_verification","EXACT NEEDS VERIFICATION"),
        ("alternatives","ALTERNATIVE"),
    ):
        for item in result.get(key) or []:
            d=dict(item); d["_bucket"]=label; buckets.append(d)
    ids=[]
    for d in buckets:
        pid=d.get("record_id") or d.get("property_id") or d.get("canonical_id") or d.get("id")
        if pid is not None: ids.append(str(pid))
    enrich=_master_contact_map(core,ids)
    return result,buckets,enrich

def _render_match(core,req):
    result,items,enrich=_match(core,req)
    cards=[]
    for d in items[:100]:
        pid=str(d.get("record_id") or d.get("property_id") or d.get("canonical_id") or d.get("id") or "")
        meta=enrich.get(pid,{})
        contacts=meta.get("contacts") or []
        contact_html="<br>".join(f"<b>{_e(x['label'])}:</b> {_e(x['value'])}" for x in contacts)
        if not contact_html: contact_html="Contact not available — verify source record"
        source=_norm(d.get("source_name") or d.get("source_bucket") or d.get("source") or ", ".join(meta.get("sources") or []))
        why=d.get("why")
        if isinstance(why,list): why=", ".join(str(x) for x in why)
        detail=d.get("detail_url") or (f"/alliance/primary/property/{quote(pid)}" if pid else "#")
        cards.append(
            "<div class='card'>"
            f"<div><b>{_e(d.get('match_class') or d.get('_bucket'))}</b> · Score {_e(d.get('match_score'))}</div>"
            f"<div><b>{_e(d.get('subtype') or d.get('family') or d.get('property') or '')}</b> · {_e(d.get('location'))} · {_e(d.get('transaction'))}</div>"
            f"<div>Area: {_e(d.get('area_display') or d.get('area_sqft') or d.get('area'))} · Price: {_e(d.get('price_display') or d.get('price'))}</div>"
            f"<div>Verification: {_e(d.get('availability_verification') or d.get('verification'))}</div>"
            f"<div>Source: {_e(source)} · Property ID: {_e(pid)}</div>"
            f"<div class='contacts'>{contact_html}</div>"
            f"<div>Why matched: {_e(why)}</div>"
            f"<div><a class='btn' href='{_e(detail)}'>View Full Property</a></div>"
            "</div>"
        )
    summary=result.get("summary") or {}
    return (
        f"<div class='card'><b>Matcher authority:</b> MASTER_ONLY · "
        f"<b>Master property database:</b> {_e(MASTER_PROPERTY_TABLE)} · "
        f"<b>Exact verified:</b> {_e(summary.get('exact_verified'))} · "
        f"<b>Needs verification:</b> {_e(summary.get('exact_needs_verification'))} · "
        f"<b>Alternatives:</b> {_e(summary.get('approved_alternatives'))}</div>"
        + "".join(cards)
    )

def _page(core,request:Request):
    rows=_requirements(core)
    cat=_norm(request.query_params.get("category") or "ALL").upper()
    allowed={"ALL","MANUAL","WHATSAPP","NEWSPAPER","DISCOVERY","OTHER"}
    if cat not in allowed: cat="ALL"
    filtered=rows if cat=="ALL" else [r for r in rows if r["category"]==cat]
    counts={k:0 for k in allowed if k!="ALL"}
    for r in rows: counts[r["category"]]=counts.get(r["category"],0)+1
    tabs=" ".join(
        f"<a class='tab' href='{WORKSPACE_ROUTE}?category={c}'>{c} ({len(rows) if c=='ALL' else counts.get(c,0)})</a>"
        for c in ("ALL","MANUAL","WHATSAPP","NEWSPAPER","DISCOVERY","OTHER")
    )
    trs=[]
    for r in filtered[:2000]:
        rid=str(r.get("id"))
        loc=", ".join(str(x) for x in r.get("locations_list") or [])
        contacts=", ".join(str(x) for x in r.get("contacts_list") or [])
        trs.append(
            "<tr>"
            f"<td>{_e(rid)}</td><td>{_e(r['category'])}</td><td>{_e(r.get('source_type'))}</td>"
            f"<td>{_e(loc)}</td><td>{_e(r.get('property_category'))}</td><td>{_e(r.get('transaction_type'))}</td>"
            f"<td>{_e(r.get('budget_min'))} - {_e(r.get('budget_max'))}</td>"
            f"<td>{_e(contacts or '—')}</td><td>{_e(r.get('classification'))}</td>"
            f"<td>{_e(_norm(r.get('original_message'))[:260])}</td>"
            f"<td><a class='btn' href='{WORKSPACE_ROUTE}?category={cat}&requirement_id={quote(rid)}#results'>Run Matcher</a></td>"
            "</tr>"
        )
    rid=_norm(request.query_params.get("requirement_id"))
    results=""
    if rid:
        req=_find_req(rows,rid)
        if req:
            results=(
                "<div id='results'><h2>Master Database Matches</h2>"
                f"<div class='card'><b>Requirement #{_e(rid)}</b><br>{_e(req.get('original_message'))}<br>"
                f"Requirement source: {_e(req.get('source_type'))} · Contact: {_e(', '.join(str(x) for x in req.get('contacts_list') or []) or '—')}</div>"
                + _render_match(core,req) + "</div>"
            )
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Master Requirement Matcher</title>
<style>body{{font-family:Arial;margin:22px;background:#f6f8fb;color:#172437}}table{{width:100%;border-collapse:collapse;background:white;font-size:12px}}th,td{{border-bottom:1px solid #e1e7ee;padding:7px;vertical-align:top;text-align:left}}th{{background:#eef3f8}}.tab,.btn{{display:inline-block;padding:8px 10px;margin:3px;border-radius:7px;background:#1769aa;color:white;text-decoration:none}}.card{{background:white;border:1px solid #dfe5eb;border-radius:10px;padding:12px;margin:10px 0}}.contacts{{margin:8px 0;padding:8px;background:#f7fafc}}</style></head><body>
<p><a href='/alliance/primary'>← Dashboard</a></p>
<h1>Alliance Master Requirement Matcher</h1>
<div class='card'>Requirement authority: <b>{MASTER_REQUIREMENT_TABLE}</b> · Property authority: <b>{MASTER_PROPERTY_TABLE}</b> · Contacts: <b>authenticated staff only</b>.</div>
<div>{tabs}</div><h3>{_e(cat)} requirements: {_e(len(filtered))}</h3>
<table><tr><th>ID</th><th>Category</th><th>Source</th><th>Location</th><th>Asset</th><th>Transaction</th><th>Budget</th><th>Requirement Contact</th><th>Status</th><th>Original Requirement</th><th>Action</th></tr>{''.join(trs)}</table>
{results}</body></html>"""

def _status(core):
    rows=_requirements(core)
    counts={}
    for r in rows: counts[r["category"]]=counts.get(r["category"],0)+1
    return {
        "status":"PASS","version":VERSION,
        "master_requirement_table":MASTER_REQUIREMENT_TABLE,
        "active_requirements":len(rows),"categories":counts,
        "master_property_table":MASTER_PROPERTY_TABLE,
        "matcher_source_contract":"MASTER_ONLY",
        "smart_matcher_route":SMART_MATCHER_ROUTE,
        "smart_matcher_target":WORKSPACE_ROUTE,
        "contacts_scope":"AUTHENTICATED_STAFF_ONLY",
    }

def register(core):
    app=_app(core)
    # Replace ONLY the legacy Smart Matcher GET surface with an authenticated redirect.
    for route in list(getattr(app.router,"routes",[])):
        if getattr(route,"path",None)==SMART_MATCHER_ROUTE and "GET" in (getattr(route,"methods",set()) or set()):
            app.router.routes.remove(route)

    @app.get(SMART_MATCHER_ROUTE)
    async def smart_matcher_redirect(request:Request):
        _auth(core,request)
        return RedirectResponse(WORKSPACE_ROUTE,status_code=302)

    existing={getattr(r,"path",None) for r in app.router.routes}
    if WORKSPACE_ROUTE not in existing:
        @app.get(WORKSPACE_ROUTE,response_class=HTMLResponse)
        async def master_requirement_page(request:Request):
            _auth(core,request)
            return HTMLResponse(_page(core,request))
    if "/api/alliance/master-requirements-v1/status" not in existing:
        @app.get("/api/alliance/master-requirements-v1/status")
        async def master_requirement_status(request:Request):
            _auth(core,request)
            return JSONResponse(_status(core))

    return {
        "status":"REGISTERED","version":VERSION,
        "requirement_authority":MASTER_REQUIREMENT_TABLE,
        "property_authority":MASTER_PROPERTY_TABLE,
        "matcher_source_contract":"MASTER_ONLY",
        "smart_matcher_takeover":True,
        "contacts_scope":"AUTHENTICATED_STAFF_ONLY",
        "routes":[WORKSPACE_ROUTE,"/api/alliance/master-requirements-v1/status",SMART_MATCHER_ROUTE],
    }
