
from __future__ import annotations

import html, json, math, re, threading, traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

import fitz
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "11.9.21-MAGAZINE-LAYOUT-REBUILD-AND-SYNC"

EVIDENCE = "pi_magazine_layout_evidence_v11921"
AUDIT = "pi_magazine_layout_audit_v11921"
RUNS = "pi_magazine_layout_runs_v11921"

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
NON_PROPERTY = {"EXCLUDE_NON_PROPERTY", "ARCHIVED", "REJECTED"}

MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)(0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|MEZZANINE|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
BHK_RE = re.compile(r"(?i)\b\d+\s*(?:BHK|BR)\b")
PTYPE_RE = re.compile(r"(?i)\b(APARTMENT|APT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|FACTORY|BUILDING|FARMHOUSE|FARM\s*HOUSE)\b")
PRICE_RE = re.compile(r"(?i)(?:₹|RS\.?|INR|@)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|L|LAC|LAKH|K|TH)\b")
AD_RE = re.compile(r"(?i)\b(REALTORS?|REALTY|ESTATES?\s+PVT|PROPERTY\s+DEALER|BUILDERS?|DEVELOPERS?|INTERIOR|CONSTRUCTION|COLLABORATION)\b")

SECTION_ASSETS = ("RESIDENTIAL","COMMERCIAL","INDUSTRIAL","RETAIL","OFFICE","HOSPITALITY","FARMHOUSE","FARM HOUSE")
TX_WORDS = ("SALE","RENT","LEASE","RENTING","SELL","BUY")

STATE_LOCK = threading.Lock()
STATE = {
    "status": "IDLE",
    "started_at": None,
    "completed_at": None,
    "uploads": 0,
    "pages": 0,
    "evidence_rows": 0,
    "unique_localities": 0,
    "master_direct_repairs": 0,
    "master_sequence_repairs": 0,
    "complete_repairs": 0,
    "unresolved_property_rows": 0,
    "error": None,
    "details": {},
}

def _utcnow():
    return datetime.now(timezone.utc).isoformat()

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _qid(s):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(s or "")):
        raise ValueError("unsafe identifier")
    return '"' + str(s) + '"'

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def _norm_key(v):
    s = _norm(v).upper()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = MOBILE_RE.sub(" ", s)
    s = LANDLINE_RE.sub(" ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _tokens(v):
    return set(_norm_key(v).split())

def _unit(raw):
    u = re.sub(r"[\s.]", "", str(raw or "").upper())
    if u in {"SQFT","FT"}: return "SQFT"
    if u in {"SQYD","YD","Y"}: return "SQYD"
    if u in {"SQM","M"}: return "SQM"
    if u == "ACRE": return "ACRE"
    return u

def _phones(v):
    s = str(v or "")
    out = set(MOBILE_RE.findall(s))
    out |= {re.sub(r"\D", "", x) for x in LANDLINE_RE.findall(s)}
    return {x for x in out if x}

def _signature(v):
    u = _norm(v).upper()
    addr = ""
    m = re.match(r"^\s*([A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", u)
    if m:
        addr = re.sub(r"\s+", "", m.group(1))
    am = AREA_RE.search(u)
    area = (am.group(1), _unit(am.group(2))) if am else ("","")
    floors = tuple(sorted(set(re.sub(r"\s+"," ",x.upper()) for x in FLOOR_RE.findall(u))))
    return addr, area, floors

def _looks_property(v):
    u = _norm(v).upper()
    if not u:
        return False
    if _is_section(u):
        return False
    addr = bool(re.match(r"^\s*(?:[A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", u))
    detail = bool(AREA_RE.search(u) or FLOOR_RE.search(u) or BHK_RE.search(u) or PTYPE_RE.search(u) or PRICE_RE.search(u))
    if AD_RE.search(u) and not detail:
        return False
    return addr and detail

def _is_section(v):
    u = _norm(v).upper()
    if not u or len(u) > 100:
        return False
    has_asset = any(re.search(rf"\b{re.escape(a)}\b", u) for a in SECTION_ASSETS)
    has_tx = any(re.search(rf"\b{re.escape(t)}\b", u) for t in TX_WORDS)
    return has_asset and has_tx

def _section_parts(v, prev=(None,None)):
    u = _norm(v).upper()
    asset = None
    tx = None
    for a in SECTION_ASSETS:
        if re.search(rf"\b{re.escape(a)}\b", u):
            asset = "Farmhouse" if a in {"FARMHOUSE","FARM HOUSE"} else a.title()
            break
    if re.search(r"\b(?:RENT|RENTING|LEASE)\b", u):
        tx = "Rent"
    elif re.search(r"\b(?:SALE|SELL|BUY)\b", u):
        tx = "Sale"
    return asset or prev[0], tx or prev[1]

def _heading_score(line, median_size, next_lines):
    text = _norm(line["text"])
    u = text.upper()
    if not text or len(text) > 75:
        return -99
    if _is_section(text) or _looks_property(text):
        return -99
    if MOBILE_RE.search(u) or LANDLINE_RE.search(u) or AREA_RE.search(u) or PRICE_RE.search(u):
        return -99
    if AD_RE.search(u):
        return -99
    if re.search(r"\b(?:MOB|PHONE|TEL|EMAIL|WWW|HTTP|PAGE)\b", u):
        return -99
    letters = sum(ch.isalpha() for ch in u)
    if letters < 3:
        return -99

    score = 0
    if text == text.upper(): score += 3
    if line["size"] >= median_size * 1.08: score += 2
    if line["bold"]: score += 2
    if len(text.split()) <= 6: score += 1
    if re.fullmatch(r"[A-Z0-9][A-Z0-9 .,&'()/+\-]{1,68}", u): score += 1

    # Strong structural signal: heading followed by one or more property rows nearby.
    following = 0
    for z in next_lines[:4]:
        if _looks_property(z["text"]):
            following += 1
    if following >= 1: score += 2
    if following >= 2: score += 2
    return score

def _canonical_locality(v):
    u = _norm(v).upper().strip(" -:|")
    if not u:
        return ""

    m = re.fullmatch(r"OKHLA(?:\s+INDUSTRIAL\s+AREA)?\s*(?:PHASE|PH\.?)?\s*[- ]?\s*(I{1,3}|[123])", u)
    if m:
        p = {"I":"1","II":"2","III":"3"}.get(m.group(1), m.group(1))
        return f"Okhla Phase {p}"

    aliases = {
        "CR PARK":"Chitranjan Park",
        "C R PARK":"Chitranjan Park",
        "GURGAON":"Gurugram",
        "NFC":"New Friends Colony",
        "CP":"Connaught Place",
    }
    if u in aliases:
        return aliases[u]

    # Preserve the specific heading. Normalize punctuation only.
    u = re.sub(r"\s*-\s*", " ", u)
    u = re.sub(r"\s+", " ", u).strip()
    protected = {"DLF","GK","NCR","NFC","CP"}
    words=[]
    for w in u.split():
        if w in protected or re.fullmatch(r"[IVX]+", w):
            words.append(w)
        else:
            words.append(w.title())
    return " ".join(words)

def _native_lines(page):
    d = page.get_text("dict", sort=True)
    out=[]
    for block in d.get("blocks",[]):
        if block.get("type") != 0:
            continue
        for line in block.get("lines",[]):
            spans = [s for s in line.get("spans",[]) if s.get("text")]
            raw = "".join(s.get("text","") for s in spans).rstrip("\r\n")
            if not raw.strip():
                continue
            bbox = line.get("bbox") or block.get("bbox") or [0,0,0,0]
            sizes = [float(s.get("size") or 0) for s in spans if s.get("size")]
            fonts = " ".join(str(s.get("font") or "") for s in spans).upper()
            out.append({
                "text": raw,
                "bbox": [float(x) for x in bbox],
                "x0": float(bbox[0]), "x1": float(bbox[2]),
                "y0": float(bbox[1]), "y1": float(bbox[3]),
                "size": max(sizes) if sizes else 0.0,
                "bold": ("BOLD" in fonts or "BLACK" in fonts or "SEMIBOLD" in fonts),
            })
    out.sort(key=lambda z:(z["y0"], z["x0"]))
    return out

def _column_id(line, width):
    center = (line["x0"] + line["x1"]) / 2.0
    f = center / max(width,1)
    return 0 if f < .34 else (1 if f < .67 else 2)

def _parse_document(doc, uid, filename):
    # Expert-style hierarchy: PAGE -> SECTION -> LOCALITY -> PROPERTY.
    # Carry context between pages per column unless an explicit heading resets it.
    ctx = {
        0: {"section":None,"asset":None,"tx":None,"locality":None},
        1: {"section":None,"asset":None,"tx":None,"locality":None},
        2: {"section":None,"asset":None,"tx":None,"locality":None},
    }
    evidence=[]
    page_count=0

    for pno in range(1, len(doc)+1):
        page = doc.load_page(pno-1)
        width = float(page.rect.width)
        lines = _native_lines(page)
        page_count += 1

        sizes = [x["size"] for x in lines if x["size"] > 0]
        median_size = sorted(sizes)[len(sizes)//2] if sizes else 10.0

        # Reading order per column. This avoids mixing unrelated column headings.
        by_col = {0:[],1:[],2:[]}
        for line in lines:
            by_col[_column_id(line,width)].append(line)
        for c in by_col:
            by_col[c].sort(key=lambda z:(z["y0"],z["x0"]))

        for col, seq in by_col.items():
            for i, line in enumerate(seq):
                raw = _norm(line["text"])
                if not raw:
                    continue

                if _is_section(raw):
                    asset, tx = _section_parts(raw, (ctx[col]["asset"],ctx[col]["tx"]))
                    ctx[col].update({"section":raw,"asset":asset,"tx":tx,"locality":None})
                    continue

                hs = _heading_score(line, median_size, seq[i+1:])
                if hs >= 5:
                    loc = _canonical_locality(raw)
                    if loc:
                        ctx[col]["locality"] = loc
                        continue

                if not _looks_property(raw):
                    continue

                evidence.append({
                    "upload_id":uid,
                    "filename":filename,
                    "page_number":pno,
                    "column_number":col,
                    "y0":line["y0"],
                    "original_text":raw,
                    "normalized_text":_norm_key(raw),
                    "section_heading":ctx[col]["section"],
                    "category":ctx[col]["asset"],
                    "transaction_type":ctx[col]["tx"],
                    "locality":ctx[col]["locality"],
                    "phones":sorted(_phones(raw)),
                    "bbox":line["bbox"],
                    "heading_confidence":100 if ctx[col]["locality"] else 0,
                })
    return evidence, page_count

def _setup(e):
    with e.begin() as c:
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(EVIDENCE)}(
            id BIGSERIAL PRIMARY KEY,
            upload_id UUID NOT NULL,
            filename TEXT,
            page_number INTEGER NOT NULL,
            column_number INTEGER NOT NULL,
            y0 DOUBLE PRECISION,
            original_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            section_heading TEXT,
            category TEXT,
            transaction_type TEXT,
            locality TEXT,
            phones JSONB NOT NULL DEFAULT '[]'::jsonb,
            bbox JSONB,
            heading_confidence INTEGER NOT NULL DEFAULT 0,
            version TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(upload_id,page_number,column_number,y0,normalized_text,version)
        )"""))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_m11921_norm ON {_qid(EVIDENCE)}(normalized_text)"))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_m11921_loc ON {_qid(EVIDENCE)}(locality)"))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(
            id BIGSERIAL PRIMARY KEY,
            target_table TEXT NOT NULL,
            target_pk TEXT NOT NULL,
            before_locality TEXT,
            after_locality TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            rule TEXT NOT NULL,
            evidence_upload_id TEXT,
            evidence_page INTEGER,
            evidence_text TEXT,
            version TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(target_table,target_pk,after_locality,version)
        )"""))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(RUNS)}(
            id BIGSERIAL PRIMARY KEY,
            version TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )"""))

def _rebuild_evidence(e):
    with e.connect() as c:
        uploads = c.execute(text("""
            SELECT upload_id::text, filename, pdf_content
            FROM pi_magazine_fresh_uploads
            WHERE pdf_content IS NOT NULL
            ORDER BY created_at NULLS LAST, upload_id
        """)).all()

    with e.begin() as c:
        c.execute(text(f"DELETE FROM {_qid(EVIDENCE)} WHERE version=:v"), {"v":VERSION})

    pages=0; rows=0
    for uid, filename, pdf in uploads:
        doc = fitz.open(stream=bytes(pdf), filetype="pdf")
        try:
            ev, pcount = _parse_document(doc, uid, filename)
            pages += pcount
            if ev:
                with e.begin() as c:
                    for x in ev:
                        c.execute(text(f"""
                        INSERT INTO {_qid(EVIDENCE)}
                        (upload_id,filename,page_number,column_number,y0,original_text,normalized_text,
                         section_heading,category,transaction_type,locality,phones,bbox,heading_confidence,version)
                        VALUES(CAST(:uid AS UUID),:fn,:pg,:col,:y,:ot,:nt,:sec,:cat,:tx,:loc,
                               CAST(:ph AS JSONB),CAST(:bbox AS JSONB),:hc,:v)
                        ON CONFLICT DO NOTHING
                        """), {
                            "uid":x["upload_id"],"fn":x["filename"],"pg":x["page_number"],"col":x["column_number"],
                            "y":x["y0"],"ot":x["original_text"],"nt":x["normalized_text"],
                            "sec":x["section_heading"],"cat":x["category"],"tx":x["transaction_type"],
                            "loc":x["locality"],"ph":json.dumps(x["phones"]),
                            "bbox":json.dumps(x["bbox"]),"hc":x["heading_confidence"],"v":VERSION
                        })
                        rows += 1
        finally:
            doc.close()
    return len(uploads), pages, rows

def _load_ev(e):
    with e.connect() as c:
        return [dict(r) for r in c.execute(text(f"""
            SELECT id,upload_id::text,filename,page_number,column_number,y0,original_text,normalized_text,
                   section_heading,category,transaction_type,locality,phones
            FROM {_qid(EVIDENCE)}
            WHERE version=:v AND locality IS NOT NULL AND BTRIM(locality)<>''
            ORDER BY upload_id,page_number,column_number,y0,id
        """), {"v":VERSION}).mappings().all()]

def _indexes(ev):
    exact=defaultdict(list); sig=defaultdict(list); addr_phone=defaultdict(list)
    for x in ev:
        exact[x["normalized_text"]].append(x)
        addr, area, floors = _signature(x["original_text"])
        if addr and area[0]:
            sig[(addr,area,floors)].append(x)
        for p in set(x.get("phones") or []):
            if addr:
                addr_phone[(addr,str(p))].append(x)
    return exact,sig,addr_phone

def _uniq_locality(rows):
    by=defaultdict(list)
    for x in rows:
        loc=_norm(x.get("locality"))
        if loc:
            by[loc.casefold()].append(x)
    if len(by)!=1:
        return None
    vals=next(iter(by.values()))
    vals.sort(key=lambda r:(r["page_number"],r["column_number"],r["y0"]))
    return vals[0]

def _fuzzy_score(a,b):
    ka=_norm_key(a); kb=_norm_key(b)
    if not ka or not kb:
        return 0.0
    sa=set(ka.split()); sb=set(kb.split())
    j=len(sa&sb)/max(1,len(sa|sb))
    seq=SequenceMatcher(None,ka,kb).ratio()
    return max(j,seq)

def _choose(desc, indexes, ev):
    exact,sig,addr_phone=indexes
    key=_norm_key(desc)
    if key:
        x=_uniq_locality(exact.get(key,[]))
        if x: return x,100,"EXACT_NORMALIZED_TEXT"

    addr,area,floors=_signature(desc)
    if addr and area[0]:
        x=_uniq_locality(sig.get((addr,area,floors),[]))
        if x: return x,96,"ADDRESS_AREA_FLOOR"

    ps=_phones(desc)
    merged=[]
    if addr and ps:
        for p in ps:
            merged.extend(addr_phone.get((addr,p),[]))
        x=_uniq_locality(merged)
        if x: return x,94,"ADDRESS_CONTACT"

    # Unique high-similarity fallback. This is still evidence-backed.
    best=[]
    for x in ev:
        score=_fuzzy_score(desc,x["original_text"])
        if score>=0.84:
            best.append((score,x))
    best.sort(key=lambda z:z[0],reverse=True)
    if best:
        top=best[0][0]
        near=[x for s,x in best if s>=max(0.84,top-0.02)]
        x=_uniq_locality(near)
        if x and (len(best)==1 or top-best[1][0]>=0.04):
            return x,88,"UNIQUE_HIGH_TEXT_SIMILARITY"
    return None,0,None

def _audit(c, table, pk, before, after, conf, rule, ev):
    c.execute(text(f"""
    INSERT INTO {_qid(AUDIT)}
    (target_table,target_pk,before_locality,after_locality,confidence,rule,
     evidence_upload_id,evidence_page,evidence_text,version)
    VALUES(:t,:pk,:b,:a,:c,:r,:u,:p,:e,:v)
    ON CONFLICT DO NOTHING
    """), {
        "t":table,"pk":str(pk),"b":before,"a":after,"c":conf,"r":rule,
        "u":ev.get("upload_id") if ev else None,
        "p":ev.get("page_number") if ev else None,
        "e":ev.get("original_text") if ev else None,
        "v":VERSION,
    })

def _repair_complete(e, ev, indexes):
    with e.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
        SELECT id,property_id,original_description,description,location,record_status
        FROM pi_magazine_complete_v860
        WHERE COALESCE(record_status,'ACTIVE')<>'ARCHIVED'
        """)).mappings().all()]
    applied=0
    with e.begin() as c:
        for r in rows:
            desc=r.get("original_description") or r.get("description") or ""
            x,conf,rule=_choose(desc,indexes,ev)
            if not x: continue
            new=_norm(x.get("locality")); old=_norm(r.get("location"))
            if not new or new.casefold()==old.casefold(): continue
            c.execute(text("""
            UPDATE pi_magazine_complete_v860
            SET location=:loc, location_source='LAYOUT_REBUILD_11921', updated_at=NOW()
            WHERE id=:id
            """), {"loc":new,"id":r["id"]})
            _audit(c,"pi_magazine_complete_v860",r["property_id"],old,new,conf,rule,x)
            applied+=1
    return applied

def _master_meta(e):
    with e.connect() as c:
        cols=[x[0] for x in c.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='pi_magazine_master'
        """)).all()]
    low={x.lower():x for x in cols}
    return (
        low.get("source_id") or low.get("id") or low.get("record_id"),
        low.get("original_raw_text") or low.get("original_description") or low.get("description"),
        low.get("locality") or low.get("location"),
        low.get("record_status") or low.get("verification_status"),
        low.get("import_batch"),
    )

def _repair_master(e, ev, indexes):
    pk,desc_col,loc_col,status_col,batch_col=_master_meta(e)
    if not all([pk,desc_col,loc_col]):
        return 0,0,0

    sel=[pk,desc_col,loc_col]
    if status_col: sel.append(status_col)
    if batch_col: sel.append(batch_col)
    with e.connect() as c:
        rows=[dict(r) for r in c.execute(text(
            "SELECT "+",".join(_qid(x) for x in sel)+" FROM pi_magazine_master"
        )).mappings().all()]

    direct=0
    anchors={}
    with e.begin() as c:
        for r in rows:
            st=_norm(r.get(status_col)).upper() if status_col else ""
            desc=r.get(desc_col) or ""
            if st in NON_PROPERTY or not _looks_property(desc):
                continue
            x,conf,rule=_choose(desc,indexes,ev)
            if not x:
                continue
            new=_norm(x.get("locality")); old=_norm(r.get(loc_col))
            if not new:
                continue
            if old.casefold()!=new.casefold():
                c.execute(text(
                    f"UPDATE pi_magazine_master SET {_qid(loc_col)}=:v WHERE CAST({_qid(pk)} AS TEXT)=:pk"
                ), {"v":new,"pk":str(r[pk])})
                _audit(c,"pi_magazine_master",r[pk],old,new,conf,rule,x)
                direct+=1
            m=re.search(r"(\d+)$",str(r[pk]))
            if m:
                anchors[int(m.group(1))]=(new, x)

    # Sequence repair: use high-confidence anchors to map interior rows to the
    # source evidence sequence. This repairs long blocks without inventing locality.
    # Each gap must have matching property-row counts and no locality ambiguity.
    with e.connect() as c:
        rows2=[dict(r) for r in c.execute(text(
            "SELECT "+",".join(_qid(x) for x in sel)+" FROM pi_magazine_master"
        )).mappings().all()]

    seq=[]
    for r in rows2:
        m=re.search(r"(\d+)$",str(r[pk]))
        if not m: continue
        r["_n"]=int(m.group(1))
        seq.append(r)
    seq.sort(key=lambda z:z["_n"])

    # Evidence ordered globally by upload/page/column/y. Build position for anchor texts.
    evpos={}
    for i,x in enumerate(ev):
        evpos.setdefault(x["normalized_text"],[]).append(i)

    anchor_pairs=[]
    for r in seq:
        st=_norm(r.get(status_col)).upper() if status_col else ""
        desc=r.get(desc_col) or ""
        if st in NON_PROPERTY or not _looks_property(desc):
            continue
        key=_norm_key(desc)
        pos=evpos.get(key,[])
        if len(pos)==1:
            anchor_pairs.append((r["_n"], pos[0]))
    anchor_pairs.sort()

    seq_by_n={r["_n"]:r for r in seq}
    sequence_repairs=0
    for (mn1,ep1),(mn2,ep2) in zip(anchor_pairs,anchor_pairs[1:]):
        if mn2<=mn1 or ep2<=ep1:
            continue
        # Keep windows tight enough to avoid crossing unrelated source batches.
        if mn2-mn1>80 or ep2-ep1>80:
            continue

        masters=[]
        for n in range(mn1+1,mn2):
            r=seq_by_n.get(n)
            if not r: continue
            st=_norm(r.get(status_col)).upper() if status_col else ""
            if st in NON_PROPERTY or not _looks_property(r.get(desc_col) or ""):
                continue
            masters.append(r)

        evid=[x for x in ev[ep1+1:ep2] if _norm(x.get("locality"))]
        if not masters or len(masters)!=len(evid):
            continue

        # Require the text sequence to be broadly consistent. This is a structural
        # alignment check, not a blind source-id interpolation.
        similarities=[_fuzzy_score(m.get(desc_col) or "",x["original_text"]) for m,x in zip(masters,evid)]
        if sum(1 for s in similarities if s>=0.55) < max(1, math.ceil(len(similarities)*0.70)):
            continue

        with e.begin() as c:
            for m,x,sim in zip(masters,evid,similarities):
                new=_norm(x.get("locality")); old=_norm(m.get(loc_col))
                if not new or old.casefold()==new.casefold():
                    continue
                # Sequence-aligned rows can correct MISSING or stale values, but
                # confidence remains below direct exact matches.
                c.execute(text(
                    f"UPDATE pi_magazine_master SET {_qid(loc_col)}=:v WHERE CAST({_qid(pk)} AS TEXT)=:pk"
                ), {"v":new,"pk":str(m[pk])})
                _audit(c,"pi_magazine_master",m[pk],old,new,82,
                       "SEQUENCE_ALIGNMENT_BETWEEN_EXACT_SOURCE_ANCHORS",x)
                sequence_repairs+=1

    # Count remaining genuine unresolved rows.
    with e.connect() as c:
        unresolved=c.execute(text(
            f"SELECT COUNT(*) FROM pi_magazine_master "
            f"WHERE UPPER(TRIM(COALESCE(CAST({_qid(loc_col)} AS TEXT),''))) "
            f"IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
        )).scalar() or 0

    return direct,sequence_repairs,int(unresolved)

def _run(core):
    e=_engine(core)
    if e is None:
        return
    with STATE_LOCK:
        if STATE["status"]=="RUNNING":
            return
        STATE.update({"status":"RUNNING","started_at":_utcnow(),"completed_at":None,"error":None,"details":{}})

    run_id=None
    try:
        _setup(e)
        with e.begin() as c:
            run_id=c.execute(text(f"""
            INSERT INTO {_qid(RUNS)}(version,status,started_at)
            VALUES(:v,'RUNNING',NOW()) RETURNING id
            """),{"v":VERSION}).scalar()

        uploads,pages,evidence_rows=_rebuild_evidence(e)
        ev=_load_ev(e)
        indexes=_indexes(ev)

        complete_repairs=_repair_complete(e,ev,indexes)
        direct,sequence,unresolved=_repair_master(e,ev,indexes)

        STATE.update({
            "status":"PASS",
            "completed_at":_utcnow(),
            "uploads":uploads,
            "pages":pages,
            "evidence_rows":evidence_rows,
            "unique_localities":len({x["locality"] for x in ev if x.get("locality")}),
            "complete_repairs":complete_repairs,
            "master_direct_repairs":direct,
            "master_sequence_repairs":sequence,
            "unresolved_property_rows":unresolved,
            "details":{
                "architecture":"PDF layout -> section -> locality -> property -> source-backed sync",
                "confidence_rules":{
                    "100":"exact normalized source text",
                    "96":"address + area + floor",
                    "94":"address + contact",
                    "88":"unique high text similarity",
                    "82":"sequence alignment between exact source anchors",
                },
                "safety":"ambiguous source relationships remain unresolved; no locality is fabricated",
            },
        })

        with e.begin() as c:
            c.execute(text(f"""
            UPDATE {_qid(RUNS)}
            SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB)
            WHERE id=:id
            """),{"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})

    except Exception as exc:
        STATE["status"]="ERROR"
        STATE["completed_at"]=_utcnow()
        STATE["error"]=f"{type(exc).__name__}: {exc}"
        STATE["details"]={"trace":traceback.format_exc()[-7000:]}
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"""
                    UPDATE {_qid(RUNS)}
                    SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB)
                    WHERE id=:id
                    """),{"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
            except Exception:
                pass

def _start(core):
    threading.Thread(target=_run,args=(core,),daemon=True,name="magazine-layout-11921").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None:
        raise RuntimeError("11.9.21 requires app + engine")
    _setup(e)

    @app.get("/alliance/admin/magazine-layout-rebuild",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req)
        s=dict(STATE)
        body=f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Magazine Layout Rebuild</title><style>
        body{{font-family:Arial;background:#f4f1eb;color:#26221d;margin:0}}
        main{{max-width:1050px;margin:32px auto;background:#fff;padding:24px;border-radius:14px}}
        .g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}
        .c{{border:1px solid #ddd;border-radius:10px;padding:14px}}
        button{{padding:10px 16px;border:0;border-radius:8px;cursor:pointer}}
        pre{{white-space:pre-wrap;background:#f7f7f7;padding:14px;border-radius:10px}}
        </style></head><body><main>
        <h2>Alliance Magazine Layout Rebuild · 11.9.21</h2>
        <p>Full-source recovery using document layout hierarchy and source-backed synchronization.</p>
        <div class='g'>
        <div class='c'><b>Status</b><br>{html.escape(str(s.get("status")))}</div>
        <div class='c'><b>PDFs</b><br>{s.get("uploads",0)}</div>
        <div class='c'><b>Pages</b><br>{s.get("pages",0)}</div>
        <div class='c'><b>Localities</b><br>{s.get("unique_localities",0)}</div>
        <div class='c'><b>Direct repairs</b><br>{s.get("master_direct_repairs",0)}</div>
        <div class='c'><b>Sequence repairs</b><br>{s.get("master_sequence_repairs",0)}</div>
        </div>
        <p><button onclick='run()'>Run Full Rebuild Again</button></p>
        <pre id='o'>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <script>
        async function run(){{let r=await fetch('/api/alliance/admin/magazine-layout-rebuild/run',{{method:'POST'}});
        o.textContent=JSON.stringify(await r.json(),null,2);setTimeout(()=>location.reload(),3000);}}
        </script></main></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store"})

    @app.get("/api/alliance/admin/magazine-layout-rebuild/status")
    def status(req:Request):
        _login(core,req)
        return JSONResponse(dict(STATE))

    @app.post("/api/alliance/admin/magazine-layout-rebuild/run")
    def run(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING":
            return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core)
        return {"status":"STARTED","version":VERSION}

    _start(core)
    return {
        "status":"REGISTERED",
        "version":VERSION,
        "auto_rebuild":True,
        "admin_url":"/alliance/admin/magazine-layout-rebuild",
    }
