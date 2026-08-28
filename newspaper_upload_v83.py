from __future__ import annotations
import io, hashlib, json, re
from datetime import datetime
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from PIL import Image, ImageOps
import newspaper_intelligence as legacy

VERSION="8.4-INTEGRATED-CAPTURE-DATABASE"

def register(core):
    app=core.app
    engine=core.engine
    need_login=core.need_login
    page_role_or_redirect=core.page_role_or_redirect
    router=APIRouter()

    def ensure_schema():
        try:
            with engine.begin() as c:
                for stmt in [x.strip() for x in legacy.SCHEMA.split(';') if x.strip()]:
                    c.execute(text(stmt))
            return True,None
        except Exception as e:
            return False,f"{type(e).__name__}: {e}"

    def generate(client, requested_model, contents):
        models=[]
        for m in [
            requested_model,
            getattr(core,"NEWSPAPER_GEMINI_MODEL",None),
            getattr(core,"GEMINI_MODEL",None),
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
        ]:
            if m and m not in models:
                models.append(m)
        errors=[]
        for m in models:
            try:
                r=client.models.generate_content(model=m,contents=contents)
                return r,m
            except Exception as e:
                errors.append(f"{m}: {type(e).__name__}: {e}")
        raise RuntimeError("All configured Gemini Vision models failed. "+" | ".join(errors))

    def extract(client,img,requested_model,high=True):
        r,model=generate(client,requested_model,[legacy.SYSTEM_PROMPT,img])
        raw=legacy._resp_text(r)
        try:
            first=legacy.parse_json_array(raw)
        except Exception as e:
            raise RuntimeError(f"JSON_PARSE_FAILED: {e}; AI_TEXT={raw[:1200]}")
        if not high:
            return first,raw,model
        existing=json.dumps(first,ensure_ascii=False)[:25000]
        prompt=f"""Perform a second independent newspaper coverage pass.
Already extracted:
{existing}
Return ONLY missed property records as a JSON array using the exact same schema.
Do not repeat existing records. Same broker phone can represent multiple different properties.
If none are missed return [].
"""
        r2,model2=generate(client,model,[prompt,img])
        raw2=legacy._resp_text(r2)
        try:
            second=legacy.parse_json_array(raw2)
        except Exception as e:
            # A valid first pass should still be saved if second audit fails.
            second=[]
            raw2=f"SECOND_PASS_WARNING: {type(e).__name__}: {e}"
        return first+second, raw+"\n\n--- SECOND PASS ---\n"+raw2, model2

    def safe(v):
        if v is None:return ""
        if hasattr(v,"isoformat"):
            try:return v.isoformat()
            except Exception:pass
        return str(v)

    def ordinal_date(v):
        if not v:return "—"
        try:
            if isinstance(v,str):
                d=datetime.fromisoformat(v[:10])
            elif hasattr(v,"day"):
                d=v
            else:return safe(v)
            day=d.day
            suffix="th" if 11<=day%100<=13 else {1:"st",2:"nd",3:"rd"}.get(day%10,"th")
            return f"{day}{suffix} {d.strftime('%b %Y')}"
        except Exception:
            return safe(v)

    def load_rows(q="",limit=2000):
        ok,err=ensure_schema()
        if not ok:return [],err
        sql="""SELECT id,record_id,date_captured,lead_type,locality,area,configuration_details,price,
          agency_brand,contact_person,phone_numbers,notes,source,completeness,verification,team_member,created_at
          FROM pi_newspaper_properties"""
        params={"lim":limit}
        if q.strip():
            sql += """ WHERE CONCAT_WS(' ',record_id,lead_type,locality,area,configuration_details,price,
              agency_brand,contact_person,phone_numbers,notes,source,verification) ILIKE :q"""
            params["q"]="%"+q.strip()+"%"
        sql+=" ORDER BY created_at DESC,id DESC LIMIT :lim"
        try:
            with engine.connect() as c:
                return [dict(r) for r in c.execute(text(sql),params).mappings().all()],None
        except Exception as e:
            return [],f"{type(e).__name__}: {e}"

    def esc(v):
        return str(v or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")

    @router.get("/api/newspaper-v83/health")
    def health(req:Request):
        need_login(req)
        ok,err=ensure_schema()
        key=bool(getattr(core,"GEMINI_API_KEY",""))
        rows,read_err=load_rows("",1) if ok else ([],err)
        return {
            "version":VERSION,
            "status":"OK" if ok and key and read_err is None else "DEGRADED",
            "schema_ready":ok,
            "schema_error":err,
            "database_read_error":read_err,
            "gemini_key_configured":key,
            "fallback_models":[
                getattr(core,"NEWSPAPER_GEMINI_MODEL",None),
                getattr(core,"GEMINI_MODEL",None),
                "gemini-3.1-flash-lite","gemini-2.5-flash-lite","gemini-2.5-flash"
            ],
            "integrated_database":True,
        }

    @router.get("/newspaper-v83",response_class=HTMLResponse)
    def page(req:Request,q:str=Query("",max_length=500)):
        if not page_role_or_redirect(req): return RedirectResponse("/login",303)
        rows,err=load_rows(q)
        trs=[]
        for r in rows:
            trs.append(f"""<tr>
              <td>{esc(ordinal_date(r.get('date_captured') or r.get('created_at')))}</td>
              <td>{esc(r.get('lead_type'))}</td><td class=loc>{esc(r.get('locality'))}</td>
              <td>{esc(r.get('area'))}</td><td class=desc>{esc(r.get('configuration_details'))}</td>
              <td>{esc(r.get('price'))}</td><td>{esc(r.get('agency_brand'))}</td>
              <td>{esc(r.get('contact_person'))}</td><td>{esc(r.get('phone_numbers'))}</td>
              <td>{esc(r.get('verification'))}</td><td>{esc(r.get('record_id'))}</td></tr>""")
        body=f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
        <title>Newspaper Property Capture & Database</title><style>
        *{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#efe4d2;color:#2d261f}}header{{background:#5d4937;color:#fff;padding:16px 20px}}
        .wrap{{max-width:1850px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #d8cab8;border-radius:14px;padding:18px;margin-bottom:14px}}
        input{{padding:9px;border:1px solid #ccb9a6;border-radius:7px}}.file{{width:100%}}.btn,button{{background:#865f3d;color:#fff;border:0;border-radius:8px;padding:10px 13px;font-weight:800;text-decoration:none;cursor:pointer}}
        .green{{background:#16845b}}.status{{white-space:pre-wrap;background:#f7f1e8;border:1px solid #dfd1bf;padding:12px;border-radius:9px;margin-top:12px}}
        .scroll{{overflow:auto;max-height:68vh}}table{{width:100%;min-width:1500px;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #eee1d1;text-align:left;vertical-align:top;font-size:12px}}
        th{{background:#f7ecdf;position:sticky;top:0}}.desc{{min-width:350px;max-width:600px}}.loc{{font-weight:800}}.top{{display:flex;gap:8px;flex-wrap:wrap}}
        </style></head><body><header><b>Newspaper Property Capture & Database V8.4</b><br><small>One page: Upload → AI extraction → Clean Newspaper Database</small></header><div class=wrap>
        <div class="card top"><a class=btn href="/workspace">← Dashboard</a><a class=btn href="#newspaper-database">Newspaper Database</a><a class=btn href="/api/newspaper-v83/health">Health</a></div>
        <div class=card><h2>Upload Newspaper Picture</h2><p>Upload the full newspaper page. The original image is saved before AI extraction.</p>
        <form id=f><input class=file type=file name=file accept="image/*" capture="environment" required><br><br>
        <input name=source_label value="Newspaper - Property"><label><input type=checkbox name=high_accuracy checked> High Accuracy second pass</label><br><br>
        <button>Upload & Process</button></form><div id=s class=status>Ready.</div></div>
        <div class=card id=newspaper-database><h2>Newspaper Database</h2>
        <form method=get><input name=q value="{esc(q)}" placeholder="Search locality, broker, phone, price, description..." style="min-width:420px"><button>Search</button></form>
        <p><b>{len(rows)}</b> records displayed. {esc(err or '')}</p>
        <div class=scroll><table><tr><th>Date</th><th>Lead Type</th><th>Locality</th><th>Area</th><th>Description</th><th>Price</th><th>Agency</th><th>Contact</th><th>Phone</th><th>Verification</th><th>ID</th></tr>
        {''.join(trs) or '<tr><td colspan=11>No newspaper records found.</td></tr>'}</table></div></div>
        <script>
        f.onsubmit=async e=>{{e.preventDefault();s.textContent='UPLOAD: sending image...';let fd=new FormData(f);fd.set('high_accuracy',f.high_accuracy.checked?'true':'false');
        try{{let r=await fetch('/api/newspaper-v83/process',{{method:'POST',body:fd,credentials:'include'}});let t=await r.text();let d;try{{d=JSON.parse(t)}}catch(_){{d={{detail:t}}}}
        if(!r.ok)throw Error((d.stage?d.stage+': ':'')+(d.detail||d.message||t));s.textContent='SUCCESS\\nSaved: '+d.inserted+'\\nDuplicates: '+d.skipped+'\\nModel: '+d.model+'\\nSource ID: '+d.source_id+'\\nRefreshing database...';setTimeout(()=>location.href='/newspaper-v83#newspaper-database',800)}}
        catch(err){{s.textContent='ERROR\\n'+err.message}}}}
        </script></div></body></html>"""
        return HTMLResponse(body)

    @router.get("/newspaper-v83/database")
    def db_redirect(req:Request):
        if not page_role_or_redirect(req): return RedirectResponse("/login",303)
        return RedirectResponse("/newspaper-v83#newspaper-database",303)

    @router.get("/newspaper-database-v42")
    def legacy_db_redirect(req:Request):
        if not page_role_or_redirect(req): return RedirectResponse("/login",303)
        return RedirectResponse("/newspaper-v83#newspaper-database",303)

    @router.post("/api/newspaper-v83/process")
    async def process(req:Request,file:UploadFile=File(...),source_label:str=Form("Newspaper - Property"),high_accuracy:str=Form("true")):
        need_login(req)
        ok,err=ensure_schema()
        if not ok: raise HTTPException(500,detail=f"DATABASE_SETUP: {err}")
        if not getattr(core,"GEMINI_API_KEY",""):
            raise HTTPException(500,detail="GEMINI: GEMINI_API_KEY is not configured")
        try:
            content=await file.read()
        except Exception as e:
            raise HTTPException(400,detail=f"UPLOAD_READ: {type(e).__name__}: {e}")
        if not content: raise HTTPException(400,detail="UPLOAD_READ: Empty image")
        if len(content)>20*1024*1024: raise HTTPException(413,detail="UPLOAD_READ: Maximum 20 MB")
        try:
            img=Image.open(io.BytesIO(content))
            img=ImageOps.exif_transpose(img).convert("RGB")
            if max(img.size)>3000: img.thumbnail((3000,3000),Image.Resampling.LANCZOS)
            optimized=io.BytesIO();img.save(optimized,format="JPEG",quality=90,optimize=True)
            vision_img=Image.open(io.BytesIO(optimized.getvalue())).convert("RGB")
        except Exception as e:
            raise HTTPException(400,detail=f"IMAGE_DECODE: {type(e).__name__}: {e}")

        sha=hashlib.sha256(content).hexdigest()
        filename=(file.filename or "newspaper.jpg")[:500]
        mime=(file.content_type or "image/jpeg")[:150]
        try:
            with engine.begin() as c:
                old=c.execute(text("SELECT id FROM pi_newspaper_sources WHERE source_hash=:h"),{"h":sha}).first()
                if old:
                    sid=old[0]
                    c.execute(text("""UPDATE pi_newspaper_sources SET image_content=:b,original_filename=:f,mime_type=:m,
                      source_label=:s,extraction_status='PROCESSING',error_message=NULL,updated_at=NOW() WHERE id=:id"""),
                      {"b":content,"f":filename,"m":mime,"s":source_label,"id":sid})
                else:
                    sid=c.execute(text("""INSERT INTO pi_newspaper_sources(
                      source_hash,original_filename,mime_type,image_content,source_label,extraction_status)
                      VALUES(:h,:f,:m,:b,:s,'PROCESSING') RETURNING id"""),
                      {"h":sha,"f":filename,"m":mime,"b":content,"s":source_label}).scalar_one()
        except Exception as e:
            raise HTTPException(500,detail=f"DATABASE_SAVE_SOURCE: {type(e).__name__}: {e}")

        try:
            client=getattr(core,"client",None)
            if client is None: client=core.genai.Client(api_key=core.GEMINI_API_KEY)
            requested=getattr(core,"NEWSPAPER_GEMINI_MODEL",None) or getattr(core,"GEMINI_MODEL",None) or "gemini-3.1-flash-lite"
            items,raw,active=extract(client,vision_img,requested,str(high_accuracy).lower() in {"1","true","yes","on"})
        except Exception as e:
            try:
                with engine.begin() as c:
                    c.execute(text("""UPDATE pi_newspaper_sources SET extraction_status='FAILED',error_message=:e,updated_at=NOW() WHERE id=:id"""),
                              {"e":str(e)[:4000],"id":sid})
            except Exception: pass
            raise HTTPException(500,detail=f"GEMINI_OR_JSON: {type(e).__name__}: {e}")

        try:
            unique=legacy._dedupe(items,source_label);inserted=0;skipped=0
            with engine.begin() as c:
                existing={r[0] for r in c.execute(text("SELECT fingerprint FROM pi_newspaper_properties")).fetchall()}
                for row,fp in unique:
                    if fp in existing:
                        skipped+=1;continue
                    rid="NEWS-"+hashlib.sha1((fp+str(sid)+str(inserted)).encode()).hexdigest()[:10].upper()
                    c.execute(text("""INSERT INTO pi_newspaper_properties(
                      record_id,source_id,fingerprint,lead_type,locality,area,configuration_details,price,
                      agency_brand,contact_person,phone_numbers,notes,source,completeness,verification,team_member)
                      VALUES(:rid,:sid,:fp,:lead_type,:locality,:area,:configuration_details,:price,
                      :agency_brand,:contact_person,:phone_numbers,:notes,:source,:completeness,'Unverified','')"""),
                      {"rid":rid,"sid":sid,"fp":fp,**row})
                    existing.add(fp);inserted+=1
                c.execute(text("""UPDATE pi_newspaper_sources SET ai_model=:m,extraction_status='COMPLETED',
                  extracted_records=:n,duplicate_records=:d,raw_ai_text=:raw,updated_at=NOW() WHERE id=:sid"""),
                  {"m":active,"n":inserted,"d":skipped,"raw":raw[:200000],"sid":sid})
            return {"status":"ok","version":VERSION,"inserted":inserted,"skipped":skipped,"model":active,"source_id":sid}
        except Exception as e:
            raise HTTPException(500,detail=f"DATABASE_SAVE_RECORDS: {type(e).__name__}: {e}")

    app.include_router(router)

    @app.middleware("http")
    async def newspaper_v84_takeover(request,call_next):
        if request.url.path=="/newspaper":
            return RedirectResponse("/newspaper-v83",307)
        return await call_next(request)

    return router
