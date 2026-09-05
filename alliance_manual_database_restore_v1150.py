from __future__ import annotations
import html, json
from fastapi import Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="11.5.0-MANUAL-SOURCE-RESTORE"
PATH="/alliance/final/database/manual"

def e(v):
    return html.escape("" if v is None else str(v))

def pick(d,*keys):
    low={str(k).lower():v for k,v in d.items()}
    for k in keys:
        v=low.get(k.lower())
        if v not in (None,"",[],{}): return v
    return ""

def remove_get(app,path):
    app.router.routes[:]=[r for r in list(app.router.routes)
        if not (getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()))]

def register(wrapped):
    app=wrapped.app
    core=wrapped.core
    engine=core.engine
    remove_get(app,PATH)

    @app.get(PATH,response_class=HTMLResponse)
    def manual_database(req:Request,q:str=Query(""),limit:int=Query(200,ge=1,le=500)):
        core.need_login(req)
        with engine.connect() as conn:
            exists=conn.execute(text("SELECT to_regclass('public.pi_operational_properties')")).scalar()
            if not exists:
                return HTMLResponse("<h2>Manual Property Database</h2><p>Source table not found.</p>",status_code=503)
            total=int(conn.execute(text("SELECT COUNT(*) FROM pi_operational_properties")).scalar() or 0)
            rows=conn.execute(text("""
                SELECT to_jsonb(t) AS rowdata
                FROM pi_operational_properties t
                WHERE (:q='' OR to_jsonb(t)::text ILIKE :pat)
                ORDER BY 1
                LIMIT :lim
            """),{"q":q.strip(),"pat":f"%{q.strip()}%","lim":int(limit)}).scalars().all()

        trs=[]
        for raw in rows:
            d=raw if isinstance(raw,dict) else json.loads(raw)
            vals=[
                pick(d,"id","property_id","canonical_id"),
                pick(d,"location","locality","area_name","city"),
                pick(d,"description","address","property_description","original_description"),
                pick(d,"property_category","category"),
                pick(d,"property_type","type"),
                pick(d,"area","area_sqft","size","built_up_area"),
                pick(d,"floor"),
                pick(d,"amount","rent","sale_amount","price"),
                pick(d,"owner_name","broker_name","contact_name","name"),
                pick(d,"contact_number","contact_no","phone","mobile"),
                pick(d,"created_at","entry_date","created_on"),
                pick(d,"verification_status","status","availability_status"),
                pick(d,"assigned_to","team_member"),
                pick(d,"source","source_name") or "MANUAL",
            ]
            trs.append("<tr>"+"".join(f"<td>{e(v)}</td>" for v in vals)+"</tr>")

        headers=["Property ID","Location","Description / Address","Property Category","Property Type",
                 "Area","Floor","Amount","Contact Name","Contact No.","Entry Date & Time","Status",
                 "Assigned To","Source"]
        body=f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manual Property Database · Alliance CRE</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;color:#172033;font-family:Arial;font-size:12px}}
header{{background:#102a43;color:#fff;padding:14px 18px}}nav{{background:#fff;border-bottom:1px solid #98a2b3;padding:7px;position:sticky;top:0}}
nav a,.btn,button{{background:#102a43;color:#fff;text-decoration:none;border:0;padding:7px 9px;margin-right:5px}}
.wrap{{padding:10px}}.stats{{display:flex;gap:8px;margin:8px 0}}.card{{background:#fff;border:1px solid #98a2b3;padding:10px;min-width:160px}}
.num{{font-size:26px;font-weight:800}}form{{display:flex;gap:5px;margin:8px 0}}input{{padding:7px;border:1px solid #98a2b3;min-width:320px}}
.tablebox{{overflow:auto;max-height:76vh;border:1px solid #667085}}table{{border-collapse:collapse;width:max-content;min-width:100%;background:#fff}}
th,td{{border:1px solid #98a2b3;padding:5px 6px;vertical-align:top;overflow-wrap:anywhere}}th{{background:#e9eef5;position:sticky;top:0;z-index:2}}
td:nth-child(3){{min-width:320px;max-width:480px}}tbody tr:nth-child(even) td{{background:#f8fafc}}</style></head>
<body><header><b>Alliance CRE Intelligence OS 11.5</b><br>Manual Source Database · restored directly from pi_operational_properties</header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/property-manual">Add Property</a><a href="/alliance/final/databases">Property Databases</a><a href="/commercial-intelligence">Commercial Intelligence</a></nav>
<div class="wrap"><div class="stats"><div class="card"><div class="num">{total:,}</div>Exact Manual Source Records</div></div>
<form method="get"><input name="q" value="{e(q)}" placeholder="Search all manual source fields"><input type="number" name="limit" min="1" max="500" value="{limit}"><button>Search</button></form>
<div class="tablebox"><table><thead><tr>{''.join(f'<th>{e(h)}</th>' for h in headers)}</tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="14">No manual source records found.</td></tr>'}
</tbody></table></div></div></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store, no-cache, must-revalidate, max-age=0",
                                          "X-Alliance-CRE-Version":"11.5.0"})
    # ensure this exact route wins
    route=[r for r in app.router.routes if getattr(r,"path",None)==PATH and "GET" in set(getattr(r,"methods",set()) or set())][-1]
    app.router.routes.remove(route); app.router.routes.insert(0,route)
    return {"status":"REGISTERED","version":VERSION,"manual_source":"pi_operational_properties"}
