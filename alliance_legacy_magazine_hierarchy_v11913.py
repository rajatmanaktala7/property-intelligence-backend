from __future__ import annotations
import json,re
from sqlalchemy import text

VERSION="11.9.13-LEGACY-MAGAZINE-HIERARCHY-REPAIR"
TABLE="pi_magazine_master"
SECTION_RE=re.compile(r"\b(RESIDENTIAL|COMMERCIAL|INDUSTRIAL|RETAIL|OFFICE|HOSPITALITY|FARMHOUSE)\b.*\b(SALE|RENT|LEASE)\b",re.I)
OKHLA_RE=re.compile(r"\bOKHLA(?:\s+INDUSTRIAL\s+AREA)?\s*(?:PHASE|PH\.?)?\s*[-:]?\s*(I{1,3}|[123])\b",re.I)
ADDRESS_RE=re.compile(r"^\s*(?:[A-Z]{1,3}\s*-\s*\d+[A-Z]?|\d+\s*/\s*\d+[A-Z]?|[A-Z]{1,3}\d+[A-Z]?)\b",re.I)

def _engine(core):return getattr(core,"engine",None)
def _qid(s):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(s)):raise ValueError("unsafe identifier")
    return '"'+str(s)+'"'
def _norm(s):return re.sub(r"\s+"," ",str(s or "")).strip()
def _exists(e,t):
    with e.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
def _cols(e,t):
    with e.connect() as c:return [r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:t ORDER BY ordinal_position"),{"t":t}).all()]
def _pick(cols,*names):
    low={x.lower():x for x in cols}
    for n in names:
        if n.lower() in low:return low[n.lower()]
    return None
def _section(s):
    m=SECTION_RE.search(_norm(s).upper())
    return (m.group(1).upper(),m.group(2).upper()) if m else ("","")
def _okhla(s):
    m=OKHLA_RE.search(_norm(s).upper())
    if not m:return ""
    p={"I":"1","II":"2","III":"3"}.get(m.group(1).upper(),m.group(1))
    return "Okhla Phase "+p
def _row_text(d):
    for k in ("original_raw_text","original_description","raw_line","raw_text","description","property_description","details","text"):
        if _norm(d.get(k)):return _norm(d.get(k))
    return ""
def _contexts(d):
    out=[]
    for k in ("section_heading","category_heading","transaction_heading","locality_heading","location_heading","page_heading","heading"):
        if _norm(d.get(k)):out.append(_norm(d.get(k)))
    raw=d.get("raw_json")
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except:raw={}
    if isinstance(raw,dict):
        for k in ("section_heading","category_heading","locality_heading","location_heading"):
            if _norm(raw.get(k)):out.append(_norm(raw.get(k)))
    return out
def _heading_locality(s):
    s=_norm(s)
    o=_okhla(s)
    if o:return o
    if not s or len(s)>70 or ADDRESS_RE.search(s) or _section(s)[0] or re.search(r"\d",s):return ""
    if re.fullmatch(r"[A-Z][A-Z .&'/-]{2,60}",s.upper()):
        return s.title() if s==s.upper() else s
    return ""

def repair(e):
    if not _exists(e,TABLE):return {"status":"SKIP","reason":"pi_magazine_master missing","version":VERSION}
    cols=_cols(e,TABLE)
    pk=_pick(cols,"id","record_id","property_id","magazine_id","row_id")
    loc=_pick(cols,"locality","location","locality_clean")
    tx=_pick(cols,"transaction_type","transaction","rent_or_sale","deal_type")
    cat=_pick(cols,"property_category","category")
    if not pk or not loc:return {"status":"SKIP","reason":"stable id/locality column missing","version":VERSION}
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_hierarchy_audit_v11913(
        id BIGSERIAL PRIMARY KEY,magazine_pk TEXT NOT NULL,field_name TEXT NOT NULL,before_value TEXT,
        after_value TEXT NOT NULL,evidence TEXT NOT NULL,version TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(magazine_pk,field_name,after_value,version))"""))
    order=[]
    for x in ("upload_id","source_file","file_name","page_number","page_no","page","column_id","column","line_no","line_number","row_no","id"):
        y=_pick(cols,x)
        if y and y not in order:order.append(y)
    sql="SELECT to_jsonb(t) d FROM "+_qid(TABLE)+" t"+((" ORDER BY "+",".join(_qid(x) for x in order)) if order else "")
    with e.connect() as c:rows=[r[0] for r in c.execute(text(sql)).all()]
    state={};props=[]
    for d in rows:
        if not isinstance(d,dict):continue
        key=tuple(str(d.get(_pick(cols,x) or "") or "") for x in ("upload_id","source_file","file_name","page_number","page_no","page","column_id","column"))
        st=state.setdefault(key,{"cat":"","tx":"","loc":""})
        ctx=_contexts(d); row=_row_text(d)
        for ev in ctx:
            sec,tran=_section(ev)
            if sec:st["cat"],st["tx"]=sec,tran
            lh=_heading_locality(ev)
            if lh:st["loc"]=lh
        if row and not ADDRESS_RE.search(row):
            sec,tran=_section(row)
            if sec:st["cat"],st["tx"]=sec,tran;continue
            lh=_heading_locality(row)
            if lh:st["loc"]=lh;continue
        if not row or not ADDRESS_RE.search(row):continue
        bad=lambda v:_norm(v).upper() in ("","MISSING","UNKNOWN","N/A","NA","NONE","NULL","UNSPECIFIED")
        pkv=str(d.get(pk))
        if st["loc"] and bad(d.get(loc)):props.append((pkv,loc,_norm(d.get(loc)),st["loc"],"VISIBLE_PARENT_LOCALITY"))
        if tx and st["tx"] and bad(d.get(tx)):props.append((pkv,tx,_norm(d.get(tx)),st["tx"],"VISIBLE_PARENT_SECTION"))
        if cat and st["cat"] and bad(d.get(cat)):props.append((pkv,cat,_norm(d.get(cat)),st["cat"],"VISIBLE_PARENT_SECTION"))
    applied=0
    with e.begin() as c:
        for pkv,field,before,after,evidence in props:
            q=f"UPDATE {_qid(TABLE)} SET {_qid(field)}=:v WHERE CAST({_qid(pk)} AS TEXT)=:pk AND UPPER(TRIM(COALESCE(CAST({_qid(field)} AS TEXT),''))) IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
            r=c.execute(text(q),{"v":after,"pk":pkv})
            if r.rowcount:
                applied+=1
                c.execute(text("""INSERT INTO pi_magazine_hierarchy_audit_v11913
                (magazine_pk,field_name,before_value,after_value,evidence,version)
                VALUES(:pk,:f,:b,:a,:e,:v) ON CONFLICT DO NOTHING"""),
                {"pk":pkv,"f":field,"b":before,"a":after,"e":evidence,"v":VERSION})
    return {"status":"PASS","version":VERSION,"scanned":len(rows),"proposed":len(props),"applied":applied,
            "duplicates_created":0,"rule":"SECTION -> EXACT LOCALITY -> ADDRESS -> DESCRIPTION -> CONTACT",
            "okhla":"PHASE 1/2/3 PRESERVED","unproven_rows":"LEFT MISSING"}

def register(core):
    try:return repair(_engine(core))
    except Exception as exc:return {"status":"ERROR","version":VERSION,"error":f"{type(exc).__name__}: {exc}","fail_safe":True}
