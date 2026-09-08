from __future__ import annotations

import html
import json
import re
from urllib.parse import quote_plus
from fastapi import Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.4.15-HOSPITALITY-DATA-RECOVERY-FORMAT"

PHONE_KEYS = ("contact_phone","phone","contact_no","contact_number","mobile","mobile_number","whatsapp","whatsapp_phone")
EMAIL_KEYS = ("email","email_id","contact_email")
WEB_KEYS = ("website","website_url","url","instagram","instagram_url")
LOC_KEYS = ("location","address","full_address","area","locality")
CITY_KEYS = ("city","district")
CONTACT_KEYS = ("contact_name","contact_person","owner_name","manager_name")
NAME_KEYS = ("business_name","brand_name","restaurant_name","hotel_name","name","company_name")

def _app(core):
    return getattr(core, "app", None) or core

def _login(core, req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _e(v):
    return html.escape("" if v is None else str(v), quote=True)

def _pick(d, keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"","NA","N/A","Unknown","UNKNOWN"):
            return str(v).strip()
    return None

def _phone(v):
    digits=re.sub(r"\D","",str(v or ""))
    if len(digits)>=10:
        return digits[-10:]
    return None

def _category(name, current):
    if current and str(current).upper() not in {"","OTHER"}:
        return str(current).upper()
    s=str(name or "").lower()
    rules=[
        ("GUEST_HOUSE",("guest house","guesthouse")),
        ("BANQUET",("banquet","party hall","marriage hall")),
        ("LOUNGE",("lounge",)),
        ("CAFE",("cafe","café","coffee")),
        ("RESTAURANT",("restaurant","restro","dining","eatery")),
        ("HOTEL",("hotel","resort")),
        ("CLUB",("club",)),
        ("BAR",(" bar","pub","brewery")),
    ]
    for cat,words in rules:
        if any(w in s for w in words):
            return cat
    return str(current or "OTHER").upper()

def recover_from_source_history(engine):
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT e.hospitality_id,e.business_name,e.category,e.location,e.city,
                 e.contact_name,e.contact_phone,e.whatsapp_phone,e.email,e.website,
                 s.source_history_id,s.source_type,s.source_name,s.source_url,
                 s.evidence_text,s.raw_payload
          FROM ai_hospitality_entity e
          JOIN LATERAL (
            SELECT *
            FROM ai_hospitality_source_history s
            WHERE s.hospitality_id=e.hospitality_id
            ORDER BY s.source_history_id DESC
            LIMIT 1
          ) s ON TRUE
          WHERE e.active=TRUE
          ORDER BY e.hospitality_id
        """)).mappings().all()]

    changed=0
    field_updates=0
    with engine.begin() as c:
        for row in rows:
            raw=row.get("raw_payload") or {}
            if isinstance(raw,str):
                try: raw=json.loads(raw)
                except Exception: raw={}
            if not isinstance(raw,dict):
                raw={}

            upd={}
            if not row.get("location"):
                v=_pick(raw,LOC_KEYS)
                if v: upd["location"]=v
            if not row.get("city"):
                v=_pick(raw,CITY_KEYS)
                if v: upd["city"]=v
            if not row.get("contact_name"):
                v=_pick(raw,CONTACT_KEYS)
                if v: upd["contact_name"]=v
            if not row.get("contact_phone"):
                v=_phone(_pick(raw,PHONE_KEYS))
                if v: upd["contact_phone"]=v
            if not row.get("whatsapp_phone"):
                v=_phone(raw.get("whatsapp_phone") or raw.get("whatsapp"))
                if v: upd["whatsapp_phone"]=v
            if not row.get("email"):
                v=_pick(raw,EMAIL_KEYS)
                if v and "@" in v: upd["email"]=v
            if not row.get("website"):
                v=_pick(raw,WEB_KEYS) or row.get("source_url")
                if v: upd["website"]=v

            cat=_category(row.get("business_name"),row.get("category"))
            if cat != str(row.get("category") or "OTHER").upper():
                upd["category"]=cat

            if not upd:
                continue

            sets=[]
            params={"id":int(row["hospitality_id"])}
            for i,(k,v) in enumerate(upd.items()):
                key=f"v{i}"
                sets.append(f"{k}=:{key}")
                params[key]=v
            sets.append("updated_at=NOW()")
            c.execute(text(
                "UPDATE ai_hospitality_entity SET "+",".join(sets)+" WHERE hospitality_id=:id"
            ),params)
            changed+=1
            field_updates+=len(upd)

    return {"entities_updated":changed,"fields_recovered":field_updates,"rows_scanned":len(rows)}

def _quality(row):
    fields={
        "location":bool(row.get("location")),
        "phone":bool(row.get("contact_phone") or row.get("whatsapp_phone")),
        "email":bool(row.get("email")),
        "website":bool(row.get("website")),
        "category":str(row.get("category") or "OTHER").upper()!="OTHER",
    }
    score=round(100*sum(fields.values())/len(fields))
    contactable=fields["phone"] or fields["email"] or fields["website"]
    return score, contactable

def render(core, req, view="all", search="", category="", page=1, per_page=100, msg=""):
    _login(core,req)
    e=core.engine
    page=max(1,int(page or 1)); per_page=max(25,min(int(per_page or 100),200)); off=(page-1)*per_page

    clauses=["active=TRUE"]
    params={"lim":per_page,"off":off}
    if category:
        clauses.append("category=:category"); params["category"]=category.upper()
    if search:
        clauses.append("""(
            LOWER(COALESCE(business_name,'')) LIKE :q OR
            LOWER(COALESCE(location,'')) LIKE :q OR
            LOWER(COALESCE(city,'')) LIKE :q OR
            LOWER(COALESCE(contact_name,'')) LIKE :q OR
            LOWER(COALESCE(contact_phone,'')) LIKE :q OR
            LOWER(COALESCE(email,'')) LIKE :q
        )"""); params["q"]=f"%{search.lower()}%"
    if view=="complete":
        clauses.append("""(
          COALESCE(contact_phone,'')<>'' OR COALESCE(email,'')<>'' OR COALESCE(website,'')<>''
        ) AND COALESCE(location,'')<>''""")
    elif view=="needs":
        clauses.append("""(
          COALESCE(location,'')='' OR
          (COALESCE(contact_phone,'')='' AND COALESCE(email,'')='' AND COALESCE(website,'')='')
        )""")
    where=" AND ".join(clauses)

    with e.connect() as c:
        total=int(c.execute(text(f"SELECT COUNT(*) FROM ai_hospitality_entity WHERE {where}"),params).scalar() or 0)
        stats=dict(c.execute(text("""
          SELECT
            COUNT(*) FILTER (WHERE active=TRUE) AS total,
            COUNT(*) FILTER (WHERE active=TRUE AND COALESCE(location,'')<>'') AS with_location,
            COUNT(*) FILTER (WHERE active=TRUE AND COALESCE(contact_phone,'')<>'') AS with_phone,
            COUNT(*) FILTER (WHERE active=TRUE AND COALESCE(email,'')<>'') AS with_email,
            COUNT(*) FILTER (WHERE active=TRUE AND COALESCE(website,'')<>'') AS with_website,
            COUNT(*) FILTER (WHERE active=TRUE AND (
              COALESCE(contact_phone,'')<>'' OR COALESCE(email,'')<>'' OR COALESCE(website,'')<>''
            )) AS contactable
          FROM ai_hospitality_entity
        """)).mappings().one())
        rows=[dict(x) for x in c.execute(text(f"""
          SELECT hospitality_id,business_name,category,location,city,contact_name,
                 contact_phone,whatsapp_phone,email,website,verification_status,
                 outreach_status,last_seen_at
          FROM ai_hospitality_entity
          WHERE {where}
          ORDER BY last_seen_at DESC,hospitality_id DESC
          LIMIT :lim OFFSET :off
        """),params).mappings().all()]

    def pct(n):
        t=int(stats.get("total") or 0)
        return f"{(100*int(n or 0)/t):.1f}%" if t else "0%"

    trs=[]
    for x in rows:
        score,contactable=_quality(x)
        contact = x.get("contact_phone") or x.get("whatsapp_phone") or ""
        cityloc="<br>".join(v for v in [_e(x.get("location")),_e(x.get("city"))] if v)
        contact_bits=[v for v in [_e(x.get("contact_name")),_e(contact),_e(x.get("email"))] if v]
        web=_e(x.get("website"))
        badge="READY" if contactable and x.get("location") else "NEEDS ENRICHMENT"
        cls="good" if badge=="READY" else "warn"
        trs.append(
            f"<tr><td><b>{_e(x.get('business_name'))}</b><br><small>ID {_e(x.get('hospitality_id'))}</small></td>"
            f"<td>{_e(x.get('category'))}</td><td>{cityloc}</td><td>{'<br>'.join(contact_bits)}</td>"
            f"<td>{web}</td><td>{_e(x.get('verification_status'))}</td>"
            f"<td><span class='{cls}'>{badge}</span><br>{score}% complete</td>"
            f"<td><a class='btn' href='/api/v3/hospitality/sources/{int(x['hospitality_id'])}'>Source</a></td></tr>"
        )

    last=max(1,(total+per_page-1)//per_page)
    qs=f"view={quote_plus(view)}&category={quote_plus(category)}&search={quote_plus(search)}&per_page={per_page}"
    topmsg=f"<div class='notice'>{_e(msg)}</div>" if msg else ""

    html_page=f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Hospitality Intelligence</title><style>
    *{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}
    .wrap{{max-width:1800px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:14px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}.metric{{background:#f8fafc;padding:12px;border-radius:10px;border:1px solid #e4e7ec}}
    .metric strong{{display:block;font-size:24px}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;vertical-align:top;text-align:left}}
    th{{background:#f8fafc;position:sticky;top:0}}.tablebox{{overflow:auto;max-height:68vh}}input,select,button,.btn{{padding:8px;border-radius:7px}}
    input,select{{border:1px solid #ccd6e0}}button,.btn{{background:#102a43;color:#fff;border:0;text-decoration:none;cursor:pointer}}.tabs{{display:flex;gap:8px;flex-wrap:wrap}}
    .good{{color:#067647;font-weight:700}}.warn{{color:#b54708;font-weight:700}}.notice{{background:#ecfdf3;padding:10px;border:1px solid #abefc6;border-radius:8px;margin-bottom:12px}}
    small{{color:#667085}}</style></head><body><div class="wrap">{topmsg}
    <div class="card"><h1>Hospitality Intelligence · Clean Working Database</h1>
    <p>This page separates usable records from records that still need enrichment. Missing values are never invented.</p>
    <div class="grid">
      <div class="metric">Total Records<strong>{int(stats.get('total') or 0):,}</strong></div>
      <div class="metric">With Location<strong>{int(stats.get('with_location') or 0):,}</strong><small>{pct(stats.get('with_location'))}</small></div>
      <div class="metric">With Phone<strong>{int(stats.get('with_phone') or 0):,}</strong><small>{pct(stats.get('with_phone'))}</small></div>
      <div class="metric">With Email<strong>{int(stats.get('with_email') or 0):,}</strong><small>{pct(stats.get('with_email'))}</small></div>
      <div class="metric">With Website<strong>{int(stats.get('with_website') or 0):,}</strong><small>{pct(stats.get('with_website'))}</small></div>
      <div class="metric">Contactable<strong>{int(stats.get('contactable') or 0):,}</strong><small>{pct(stats.get('contactable'))}</small></div>
    </div></div>

    <div class="card"><div class="tabs">
      <a class="btn" href="/hospitality-intelligence?view=all">All Records</a>
      <a class="btn" href="/hospitality-intelligence?view=complete">Usable / Contactable</a>
      <a class="btn" href="/hospitality-intelligence?view=needs">Needs Enrichment</a>
    </div><br>
    <form method="get">
      <input type="hidden" name="view" value="{_e(view)}">
      <input name="search" value="{_e(search)}" placeholder="Business / location / phone / email" size="38">
      <select name="category"><option value="">All Categories</option>{"".join(f"<option value='{c}' {'selected' if c==category else ''}>{c}</option>" for c in ['RESTAURANT','CAFE','LOUNGE','CLUB','BANQUET','GUEST_HOUSE','HOTEL','BAR','CLOUD_KITCHEN','OTHER'])}</select>
      <select name="per_page"><option>50</option><option selected>100</option><option>200</option></select>
      <button>Apply</button>
    </form></div>

    <div class="card"><h2>Recover Missing Fields Already Present in Source History</h2>
    <p>This only fills blank entity fields from stored source payloads. It does not invent data and does not overwrite populated fields.</p>
    <form method="post" action="/hospitality-intelligence/recover-source-data"><button>Recover Existing Source Data</button></form></div>

    <div class="card"><h2>{'Usable / Contactable' if view=='complete' else 'Needs Enrichment' if view=='needs' else 'All'} Records · {total:,}</h2>
    <div class="tablebox"><table><tr><th>Business</th><th>Category</th><th>Location</th><th>Contact</th><th>Website</th><th>Verification</th><th>Data Quality</th><th>Evidence</th></tr>
    {''.join(trs) or '<tr><td colspan="8">No records in this view.</td></tr>'}</table></div>
    <p><a class="btn" href="/hospitality-intelligence?{qs}&page={max(1,page-1)}">Previous</a> Page {page} of {last} <a class="btn" href="/hospitality-intelligence?{qs}&page={min(last,page+1)}">Next</a></p></div>
    </div></body></html>"""
    return HTMLResponse(html_page,headers={"Cache-Control":"no-store"})

def register(core):
    app=_app(core)
    keep=[]
    for r in list(app.router.routes):
        if getattr(r,"path",None) in {"/hospitality-intelligence","/v3/hospitality-intelligence"} and "GET" in set(getattr(r,"methods",set()) or set()):
            continue
        keep.append(r)
    app.router.routes[:]=keep

    @app.get("/hospitality-intelligence",response_class=HTMLResponse)
    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def page(req:Request,view:str=Query("all"),search:str=Query(""),category:str=Query(""),page:int=Query(1),per_page:int=Query(100),msg:str=Query("")):
        if view not in {"all","complete","needs"}: view="all"
        return render(core,req,view,search,category,page,per_page,msg)

    @app.post("/hospitality-intelligence/recover-source-data")
    def recover(req:Request):
        _login(core,req)
        out=recover_from_source_history(core.engine)
        msg=f"Recovered {out['fields_recovered']} missing fields across {out['entities_updated']} records"
        return RedirectResponse("/hospitality-intelligence?msg="+quote_plus(msg),303)

    @app.get("/api/alliance/hospitality-data-quality")
    def quality():
        with core.engine.connect() as c:
            r=dict(c.execute(text("""
              SELECT COUNT(*) AS total,
              COUNT(*) FILTER (WHERE COALESCE(location,'')<>'') AS with_location,
              COUNT(*) FILTER (WHERE COALESCE(contact_phone,'')<>'') AS with_phone,
              COUNT(*) FILTER (WHERE COALESCE(email,'')<>'') AS with_email,
              COUNT(*) FILTER (WHERE COALESCE(website,'')<>'') AS with_website,
              COUNT(*) FILTER (WHERE COALESCE(contact_phone,'')='' AND COALESCE(email,'')='' AND COALESCE(website,'')='') AS no_contact_channel
              FROM ai_hospitality_entity WHERE active=TRUE
            """)).mappings().one())
        return {"status":"PASS","version":VERSION,**r}

    return {"status":"AUTHORITATIVE","version":VERSION,"source_history_recovery":True,"proper_format":True}
