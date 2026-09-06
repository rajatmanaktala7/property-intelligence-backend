
from __future__ import annotations
import html, json, re
from fastapi import Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

try:
    import fitz
except Exception:
    fitz = None

VERSION = "11.9.15-MAGAZINE-EVIDENCE-ADMIN-READONLY"
TARGETS = ["SHV-L05633","SHV-L05632","SHV-L05631","SHV-L05630","SHV-L05629","SHV-L05628"]
TABLES = ["pi_magazine_master","pi_magazine_complete_v860","pi_magazine_organized_v850","pi_magazine_fastlane_records"]
DESC_KEYS = ["original_raw_text","original_description","description","property_description","clean_description"]
UPLOAD_KEYS = ["upload_id","source_upload_id"]
PAGE_KEYS = ["page_number","page_no","page"]

def _app(core): return getattr(core, "app", None) or core
def _engine(core): return getattr(core, "engine", None)
def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"
def _esc(v): return html.escape("" if v is None else str(v))
def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()

def _qid(v):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(v or "")):
        raise ValueError("unsafe identifier")
    return '"' + str(v) + '"'

def _exists(e, table):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar())

def _rows_like(e, table, needle, limit=20):
    q = f"SELECT to_jsonb(t) d FROM {_qid(table)} t WHERE to_jsonb(t)::text ILIKE :n LIMIT :lim"
    with e.connect() as c:
        return [r[0] for r in c.execute(text(q), {"n": f"%{needle}%", "lim": limit}).all()]

def _desc(d):
    if not isinstance(d, dict): return ""
    for k in DESC_KEYS:
        if _norm(d.get(k)): return _norm(d.get(k))
    return ""

def _first(d, keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d.get(k)
    return None

def _short(s): return _norm(s)[:48]

def _pdf_context(e, upload_id, page_no, desc):
    if fitz is None or not upload_id or not page_no:
        return []
    if not _exists(e, "pi_magazine_fresh_uploads"):
        return []
    try:
        with e.connect() as c:
            row = c.execute(
                text("SELECT pdf_content FROM pi_magazine_fresh_uploads WHERE CAST(upload_id AS TEXT)=:u LIMIT 1"),
                {"u": str(upload_id)}
            ).first()
        if not row or row[0] is None:
            return []
        doc = fitz.open(stream=bytes(row[0]), filetype="pdf")
        try:
            p = int(page_no)
            if p < 1 or p > len(doc):
                return []
            lines = [re.sub(r"\s+"," ",x).strip() for x in doc.load_page(p-1).get_text("text", sort=True).splitlines()]
            lines = [x for x in lines if x]
            toks = [x for x in re.findall(r"[A-Z0-9]+", _short(desc).upper()) if len(x) >= 2][:5]
            best_i, best_hits = None, -1
            for i, line in enumerate(lines):
                u=line.upper()
                hits=sum(1 for t in toks if t in u)
                if hits > best_hits:
                    best_i,best_hits=i,hits
            if best_i is None or best_hits <= 0:
                return lines[:50]
            return lines[max(0,best_i-15):min(len(lines),best_i+8)]
        finally:
            doc.close()
    except Exception as exc:
        return [f"PDF context error: {type(exc).__name__}: {exc}"]

def _card(title, body):
    return (
        "<section style='border:1px solid #ddd;border-radius:10px;padding:14px;margin:12px 0;background:#fff'>"
        f"<h3 style='margin:0 0 10px'>{_esc(title)}</h3>{body}</section>"
    )

def _render_json(d):
    if not isinstance(d, dict):
        return f"<pre>{_esc(d)}</pre>"
    keys = [
        "property_id","record_id","source_record_id","id","upload_id","page_number","page_no",
        "section_heading","original_section","locality_heading","location_heading","location","locality",
        "property_category","category","transaction_type","property_type","original_raw_text",
        "original_description","description","contact_name","contact_numbers","raw_json"
    ]
    rows=[]
    shown=set()
    for k in keys:
        if k in d and k not in shown and d.get(k) not in (None,"",[],{}):
            v=d.get(k)
            if isinstance(v,(dict,list)):
                v=json.dumps(v,ensure_ascii=False)
            rows.append(
                "<tr>"
                f"<td style='vertical-align:top;padding:4px 8px'><b>{_esc(k)}</b></td>"
                f"<td style='padding:4px 8px'>{_esc(v)}</td>"
                "</tr>"
            )
            shown.add(k)
    return "<table style='border-collapse:collapse;width:100%'>"+"".join(rows)+"</table>"

def register(core):
    app=_app(core)
    e=_engine(core)
    if app is None or e is None:
        raise RuntimeError("11.9.15 requires app + engine")

    @app.get("/alliance/admin/magazine-evidence", response_class=HTMLResponse)
    def magazine_evidence(req: Request, target: str = Query("")):
        _login(core, req)
        targets=[target.strip()] if target.strip() else TARGETS
        parts=[
            "<!doctype html><html><head><meta charset='utf-8'><title>Magazine Evidence</title></head>"
            "<body style='font-family:Arial;background:#f5f5f5;margin:0;padding:20px;max-width:1500px'>",
            f"<h2>Alliance Magazine Evidence · {_esc(VERSION)}</h2>",
            "<p><b>READ ONLY.</b> This page performs SELECT/introspection only. It does not update database records.</p>",
            f"<form method='get'><input name='target' value='{_esc(target)}' placeholder='e.g. SHV-L05633' style='padding:8px;width:260px'>"
            "<button style='padding:8px 12px'>Trace Record</button></form>"
        ]

        for target_id in targets:
            parts.append(f"<h2 style='margin-top:28px'>Trace: {_esc(target_id)}</h2>")
            master_hits=[]
            if _exists(e,"pi_magazine_master"):
                master_hits=_rows_like(e,"pi_magazine_master",target_id,10)

            if not master_hits:
                parts.append(_card("Master", "<b>Not found in pi_magazine_master</b>"))
                continue

            for i,m in enumerate(master_hits,1):
                parts.append(_card(f"Master match {i}", _render_json(m)))
                desc=_desc(m)
                needles=[_short(desc)]
                mm=re.match(r"^\s*([A-Z0-9/-]{2,15})\b", desc.upper())
                if mm:
                    needles.append(mm.group(1))

                upstream_found=False
                for table in TABLES[1:]:
                    if not _exists(e,table):
                        continue
                    seen=set()
                    for needle in needles:
                        if not needle:
                            continue
                        for d in _rows_like(e,table,needle,20):
                            marker=json.dumps(d,sort_keys=True,default=str)
                            if marker in seen:
                                continue
                            seen.add(marker)
                            upstream_found=True
                            parts.append(_card(f"Upstream · {table}", _render_json(d)))
                            upload=_first(d,UPLOAD_KEYS)
                            page=_first(d,PAGE_KEYS)
                            udesc=_desc(d) or desc
                            ctx=_pdf_context(e,upload,page,udesc)
                            if ctx:
                                txt=(
                                    "<pre style='white-space:pre-wrap;background:#111;color:#eee;padding:12px;border-radius:8px'>"
                                    + _esc("\n".join(ctx)) + "</pre>"
                                )
                                parts.append(_card(f"Stored PDF context · upload={upload} · page={page}", txt))
                if not upstream_found:
                    parts.append(_card("Upstream trace", "<b>No matching upstream record found from the retained description/address fingerprint.</b>"))

        parts.append(
            "<p style='margin-top:28px'><b>Interpretation:</b> If the stored PDF context shows a section heading and exact locality heading immediately before the property rows, that is deterministic evidence for repair. If not, the row must remain unresolved rather than guessed.</p>"
            "</body></html>"
        )
        return HTMLResponse("".join(parts),headers={"Cache-Control":"no-store"})

    return {"status":"PASS","version":VERSION,"route":"/alliance/admin/magazine-evidence","mode":"READ_ONLY"}
