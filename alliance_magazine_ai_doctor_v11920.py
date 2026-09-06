
from __future__ import annotations

import html
import json
import re
import threading
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone

import fitz
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "11.9.20-MAGAZINE-AI-DOCTOR-ALL-LOCATIONS"

EVIDENCE = "pi_magazine_hierarchy_evidence_v11920"
RUNS = "pi_magazine_ai_doctor_runs_v11920"
AUDIT = "pi_magazine_ai_doctor_audit_v11920"

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
NON_PROPERTY = {"EXCLUDE_NON_PROPERTY", "ARCHIVED", "REJECTED"}

ASSET_WORDS = r"(?:RESIDENTIAL|COMMERCIAL|INDUSTRIAL|RETAIL|OFFICE|HOSPITALITY|FARM\s*HOUSE|FARMHOUSE)"
TX_WORDS = r"(?:SALE|RENT|LEASE|RENTING|SELL|BUY)"
AREA_RE = re.compile(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|MEZZANINE|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
BHK_RE = re.compile(r"(?i)\b\d+\s*(?:BHK|BR)\b")
PRICE_RE = re.compile(r"(?i)(?:₹|RS\.?|INR|@)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|L|LAC|LAKH|K|TH)\b")
MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)(0?11[-\s]?\d{7,8})(?!\d)")
PROPERTY_TYPE_RE = re.compile(r"(?i)\b(APARTMENT|APT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|FACTORY|BUILDING|FARMHOUSE)\b")
AD_RE = re.compile(r"(?i)\b(REALTORS?|REALTY|ESTATES?\s+PVT|PROPERTY\s+DEALER|BUILDERS?|DEVELOPERS?|INTERIOR|CONSTRUCTION|COLLABORATION)\b")

LOCK = threading.Lock()
STATE = {
    "status": "IDLE",
    "started_at": None,
    "completed_at": None,
    "uploads": 0,
    "pages": 0,
    "evidence_rows": 0,
    "complete_repairs": 0,
    "master_repairs": 0,
    "future_parser_patched": False,
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


def _phone_set(v):
    s = str(v or "")
    return set(MOBILE_RE.findall(s)) | {re.sub(r"\D", "", x) for x in LANDLINE_RE.findall(s)}


def _unit(raw):
    u = re.sub(r"[\s.]", "", str(raw or "").upper())
    if u in {"SQFT", "FT"}:
        return "SQFT"
    if u in {"SQYD", "YD", "Y"}:
        return "SQYD"
    if u in {"SQM", "M"}:
        return "SQM"
    if u == "ACRE":
        return "ACRE"
    return u


def _signature(v):
    s = _norm(v).upper()
    addr = ""
    m = re.match(r"^\s*([A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", s)
    if m:
        addr = re.sub(r"\s+", "", m.group(1))
    am = AREA_RE.search(s)
    area = (am.group(1), _unit(am.group(2))) if am else ("", "")
    floors = tuple(sorted(set(
        re.sub(r"\s+", " ", x.upper())
        for x in FLOOR_RE.findall(s)
    )))
    return addr, area, floors


def _is_section_heading(v):
    u = _norm(v).upper().strip(" -:|")
    if not u or len(u) > 80:
        return False
    has_asset = bool(re.search(rf"\b{ASSET_WORDS}\b", u))
    has_tx = bool(re.search(rf"\b{TX_WORDS}\b", u))
    return has_asset and has_tx


def _section_parts(v, previous=None):
    u = _norm(v).upper()
    asset = None
    tx = None
    for a in ("RESIDENTIAL", "COMMERCIAL", "INDUSTRIAL", "RETAIL", "OFFICE", "HOSPITALITY", "FARMHOUSE"):
        if re.search(rf"\b{a}\b", u):
            asset = "Farmhouse" if a == "FARMHOUSE" else a.title()
            break
    if re.search(r"\b(?:RENT|RENTING)\b", u):
        tx = "Rent"
    elif re.search(r"\bLEASE\b", u):
        tx = "Rent"
    elif re.search(r"\b(?:SALE|SELL|BUY)\b", u):
        tx = "Sale"
    if previous:
        asset = asset or previous[0]
        tx = tx or previous[1]
    return asset, tx


def _looks_like_property(v):
    s = _norm(v)
    if not s:
        return False
    u = s.upper()
    if _is_section_heading(u):
        return False
    if AD_RE.search(u) and not (AREA_RE.search(u) or FLOOR_RE.search(u) or BHK_RE.search(u)):
        return False
    addr = bool(re.match(r"^\s*(?:[A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", u))
    detail = bool(AREA_RE.search(u) or FLOOR_RE.search(u) or BHK_RE.search(u) or PRICE_RE.search(u) or PROPERTY_TYPE_RE.search(u))
    return addr and detail


def _looks_like_locality_heading(v):
    s = _norm(v)
    if not s or len(s) > 70:
        return False
    u = s.upper().strip(" -:|")
    if _is_section_heading(u):
        return False
    if _looks_like_property(u):
        return False
    if MOBILE_RE.search(u) or LANDLINE_RE.search(u) or AREA_RE.search(u) or PRICE_RE.search(u):
        return False
    if AD_RE.search(u):
        return False
    if re.search(r"\b(?:MOB|PHONE|TEL|EMAIL|WWW|HTTP|PAGE)\b", u):
        return False
    letters = sum(ch.isalpha() for ch in u)
    if letters < 3:
        return False
    # Magazine locality headings are generally uppercase and short.
    if s != s.upper():
        return False
    if len(u.split()) > 8:
        return False
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9 .,&'()/+\-]{1,68}", u))


def _canonical_locality(v):
    u = _norm(v).upper().strip(" -:|")
    if not u:
        return ""

    # Exact phase preservation for all Okhla variants.
    m = re.fullmatch(r"OKHLA(?:\s+INDUSTRIAL\s+AREA)?\s*(?:PHASE|PH\.?)?\s*[- ]?\s*(I{1,3}|[123])", u)
    if m:
        p = {"I": "1", "II": "2", "III": "3"}.get(m.group(1), m.group(1))
        return f"Okhla Phase {p}"

    # Common magazine aliases, while preserving specificity.
    aliases = {
        "CR PARK": "Chitranjan Park",
        "C R PARK": "Chitranjan Park",
        "GURGAON": "Gurugram",
        "NFC": "New Friends Colony",
        "CP": "Connaught Place",
    }
    if u in aliases:
        return aliases[u]

    # Normalize phase/sector/block punctuation without collapsing locality detail.
    u = re.sub(r"\s*-\s*", " ", u)
    u = re.sub(r"\s+", " ", u).strip()

    protected = {"DLF", "GK", "NCR", "NFC", "CP"}
    words = []
    for w in u.split():
        if w in protected:
            words.append(w)
        elif re.fullmatch(r"[IVX]+", w):
            words.append(w)
        else:
            words.append(w.title())
    return " ".join(words)


def _column_id(x0, width):
    if width <= 0:
        return 0
    f = x0 / width
    return 0 if f < 0.34 else (1 if f < 0.67 else 2)


def _native_lines(page):
    d = page.get_text("dict", sort=True)
    out = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            raw = "".join(span.get("text", "") for span in line.get("spans", []) if span.get("text", ""))
            if not raw.strip():
                continue
            bbox = line.get("bbox") or block.get("bbox") or [0, 0, 0, 0]
            out.append({
                "text": raw.rstrip("\r\n"),
                "bbox": [float(x) for x in bbox],
                "x0": float(bbox[0]),
                "y0": float(bbox[1]),
            })
    out.sort(key=lambda z: (z["y0"], z["x0"]))
    return out


def _parse_page(page):
    lines = _native_lines(page)
    width = float(page.rect.width)
    ctx = {
        0: {"section": None, "asset": None, "tx": None, "locality": None},
        1: {"section": None, "asset": None, "tx": None, "locality": None},
        2: {"section": None, "asset": None, "tx": None, "locality": None},
    }
    out = []

    # We also keep last global section so a section heading spanning the page can
    # seed all three columns.
    global_section = None
    global_parts = (None, None)

    for row in lines:
        raw = _norm(row["text"])
        col = _column_id(row["x0"], width)

        if _is_section_heading(raw):
            global_section = raw
            global_parts = _section_parts(raw, global_parts)
            # A full-width or left-column heading usually governs the page.
            for c in ctx:
                ctx[c]["section"] = raw
                ctx[c]["asset"] = global_parts[0]
                ctx[c]["tx"] = global_parts[1]
                ctx[c]["locality"] = None
            continue

        if _looks_like_locality_heading(raw):
            loc = _canonical_locality(raw)
            if loc:
                if ctx[col]["section"] is None and global_section:
                    ctx[col]["section"] = global_section
                    ctx[col]["asset"], ctx[col]["tx"] = global_parts
                ctx[col]["locality"] = loc
            continue

        if not _looks_like_property(raw):
            continue

        c = ctx[col]
        out.append({
            "column": col,
            "y0": row["y0"],
            "bbox": row["bbox"],
            "original_text": raw,
            "normalized_text": _norm_key(raw),
            "section_heading": c["section"] or global_section,
            "category": c["asset"] or global_parts[0],
            "transaction": c["tx"] or global_parts[1],
            "locality": c["locality"],
            "phones": sorted(_phone_set(raw)),
        })
    return out


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
            version TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(upload_id,page_number,column_number,y0,normalized_text,version)
        )
        """))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_magdoc_norm ON {_qid(EVIDENCE)}(normalized_text)"))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_magdoc_loc ON {_qid(EVIDENCE)}(locality)"))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(RUNS)}(
            id BIGSERIAL PRIMARY KEY,
            version TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(
            id BIGSERIAL PRIMARY KEY,
            target_table TEXT NOT NULL,
            target_pk TEXT NOT NULL,
            field_name TEXT NOT NULL,
            before_value TEXT,
            after_value TEXT,
            evidence_upload_id TEXT,
            evidence_page INTEGER,
            evidence_text TEXT,
            rule TEXT NOT NULL,
            version TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(target_table,target_pk,field_name,after_value,version)
        )
        """))


def _load_uploads(e):
    with e.connect() as c:
        rows = c.execute(text("""
            SELECT upload_id::text, filename, pdf_content
            FROM pi_magazine_fresh_uploads
            WHERE pdf_content IS NOT NULL
            ORDER BY created_at NULLS LAST, upload_id
        """)).all()
    return rows


def _rebuild_evidence(e):
    uploads = _load_uploads(e)
    total_pages = 0
    total_rows = 0

    # Version-scoped rebuild. Old evidence is retained for auditability.
    with e.begin() as c:
        c.execute(text(f"DELETE FROM {_qid(EVIDENCE)} WHERE version=:v"), {"v": VERSION})

    for uid, filename, pdf_content in uploads:
        doc = fitz.open(stream=bytes(pdf_content), filetype="pdf")
        try:
            for pno in range(1, len(doc) + 1):
                page = doc.load_page(pno - 1)
                parsed = _parse_page(page)
                total_pages += 1
                if not parsed:
                    continue
                with e.begin() as c:
                    for x in parsed:
                        c.execute(text(f"""
                            INSERT INTO {_qid(EVIDENCE)}
                            (upload_id,filename,page_number,column_number,y0,original_text,normalized_text,
                             section_heading,category,transaction_type,locality,phones,bbox,version)
                            VALUES(CAST(:uid AS UUID),:fn,:pg,:col,:y,:ot,:nt,:sec,:cat,:tx,:loc,
                                   CAST(:ph AS JSONB),CAST(:bbox AS JSONB),:ver)
                            ON CONFLICT DO NOTHING
                        """), {
                            "uid": uid, "fn": filename, "pg": pno, "col": x["column"], "y": x["y0"],
                            "ot": x["original_text"], "nt": x["normalized_text"],
                            "sec": x["section_heading"], "cat": x["category"], "tx": x["transaction"],
                            "loc": x["locality"], "ph": json.dumps(x["phones"]),
                            "bbox": json.dumps(x["bbox"]), "ver": VERSION,
                        })
                        total_rows += 1
        finally:
            doc.close()

    return {"uploads": len(uploads), "pages": total_pages, "evidence_rows": total_rows}


def _evidence_rows(e):
    with e.connect() as c:
        return [dict(r) for r in c.execute(text(f"""
            SELECT id,upload_id::text,filename,page_number,column_number,y0,original_text,normalized_text,
                   section_heading,category,transaction_type,locality,phones
            FROM {_qid(EVIDENCE)}
            WHERE version=:v AND locality IS NOT NULL AND BTRIM(locality)<>''
            ORDER BY upload_id,page_number,column_number,y0,id
        """), {"v": VERSION}).mappings().all()]


def _candidate_indexes(ev):
    exact = defaultdict(list)
    sig = defaultdict(list)
    addr_phone = defaultdict(list)
    locality_names = Counter()

    for x in ev:
        exact[x["normalized_text"]].append(x)
        addr, area, floors = _signature(x["original_text"])
        if addr and area[0]:
            sig[(addr, area, floors)].append(x)
        phones = set(x.get("phones") or [])
        if addr:
            for p in phones:
                addr_phone[(addr, str(p))].append(x)
        if x.get("locality"):
            locality_names[x["locality"]] += 1

    return exact, sig, addr_phone, locality_names


def _unique_locality(cands):
    by = defaultdict(list)
    for x in cands:
        loc = _norm(x.get("locality"))
        if loc:
            by[loc.casefold()].append(x)
    if len(by) != 1:
        return None
    rows = next(iter(by.values()))
    rows.sort(key=lambda r: (r.get("page_number") or 0, r.get("column_number") or 0, r.get("y0") or 0))
    return rows[0]


def _direct_locality_from_text(desc, locality_names):
    u = _norm(desc).upper()
    hits = []
    for loc, _n in locality_names.most_common():
        lu = loc.upper()
        variants = {lu, lu.replace(" PHASE ", "-"), lu.replace(" ", "-")}
        for v in variants:
            if len(v) >= 5 and re.search(r"(?<![A-Z0-9])" + re.escape(v) + r"(?![A-Z0-9])", u):
                hits.append(loc)
                break
    hits = list(dict.fromkeys(hits))
    return hits[0] if len(hits) == 1 else None


def _choose_candidate(desc, exact, sig, addr_phone, locality_names):
    key = _norm_key(desc)
    if key:
        w = _unique_locality(exact.get(key, []))
        if w:
            return w, "EXACT_NORMALIZED_TEXT"

    addr, area, floors = _signature(desc)
    if addr and area[0]:
        w = _unique_locality(sig.get((addr, area, floors), []))
        if w:
            return w, "ADDRESS_AREA_FLOOR_SIGNATURE"

    phones = _phone_set(desc)
    if addr and phones:
        merged = []
        for p in phones:
            merged.extend(addr_phone.get((addr, p), []))
        w = _unique_locality(merged)
        if w:
            return w, "ADDRESS_PLUS_CONTACT"

    direct = _direct_locality_from_text(desc, locality_names)
    if direct:
        return {"locality": direct, "upload_id": None, "page_number": None, "original_text": desc}, "DIRECT_LOCALITY_IN_DESCRIPTION"

    return None, None


def _audit(c, table, pk, field, before, after, ev, rule):
    c.execute(text(f"""
        INSERT INTO {_qid(AUDIT)}
        (target_table,target_pk,field_name,before_value,after_value,evidence_upload_id,
         evidence_page,evidence_text,rule,version)
        VALUES(:t,:pk,:f,:b,:a,:u,:p,:e,:r,:v)
        ON CONFLICT DO NOTHING
    """), {
        "t": table, "pk": str(pk), "f": field, "b": before, "a": after,
        "u": ev.get("upload_id") if ev else None,
        "p": ev.get("page_number") if ev else None,
        "e": ev.get("original_text") if ev else None,
        "r": rule, "v": VERSION,
    })


def _repair_complete(e, indexes):
    exact, sig, addr_phone, locality_names = indexes
    with e.connect() as c:
        rows = [dict(r) for r in c.execute(text("""
            SELECT id,property_id,upload_id::text,page_number,original_description,description,
                   location,original_section,property_category,verification_status,record_status
            FROM pi_magazine_complete_v860
            WHERE COALESCE(record_status,'ACTIVE') <> 'ARCHIVED'
        """)).mappings().all()]

    applied = 0
    with e.begin() as c:
        for r in rows:
            desc = r.get("original_description") or r.get("description") or ""
            ev, rule = _choose_candidate(desc, exact, sig, addr_phone, locality_names)
            if not ev:
                continue
            newloc = _norm(ev.get("locality"))
            if not newloc:
                continue
            old = _norm(r.get("location"))
            newsec = _norm(ev.get("section_heading"))
            updates = []
            params = {"id": r["id"], "loc": newloc, "sec": newsec}
            if old.casefold() != newloc.casefold():
                updates.append("location=:loc")
            if newsec and _norm(r.get("original_section")).casefold() != newsec.casefold():
                updates.append("original_section=:sec")
            if not updates:
                continue
            updates.append("location_source='AI_DOCTOR_SOURCE_HIERARCHY'")
            c.execute(text("UPDATE pi_magazine_complete_v860 SET " + ",".join(updates) + ",updated_at=NOW() WHERE id=:id"), params)
            if old.casefold() != newloc.casefold():
                _audit(c, "pi_magazine_complete_v860", r["property_id"], "location", old, newloc, ev, rule)
                applied += 1
    return applied


def _repair_master(e, indexes):
    exact, sig, addr_phone, locality_names = indexes

    # Introspect legacy master because this is the visible /alliance/source/magazine table.
    with e.connect() as c:
        cols = [x[0] for x in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='pi_magazine_master'
        """)).all()]
    low = {x.lower(): x for x in cols}
    pk = low.get("source_id") or low.get("id") or low.get("record_id")
    desc_col = low.get("original_raw_text") or low.get("original_description") or low.get("description")
    loc_col = low.get("locality") or low.get("location")
    status_col = low.get("record_status") or low.get("verification_status")
    if not all([pk, desc_col, loc_col]):
        return 0, {"status": "SKIP", "reason": "legacy master columns missing"}

    select_cols = [pk, desc_col, loc_col]
    if status_col:
        select_cols.append(status_col)
    with e.connect() as c:
        rows = [dict(r) for r in c.execute(text(
            "SELECT " + ",".join(_qid(x) for x in select_cols) + " FROM pi_magazine_master"
        )).mappings().all()]

    # First pass: source-backed direct repairs.
    anchored = {}
    applied = 0
    proposals = []

    for r in rows:
        st = _norm(r.get(status_col)).upper() if status_col else ""
        if st in NON_PROPERTY:
            continue
        d = r.get(desc_col) or ""
        if not _looks_like_property(d):
            continue
        ev, rule = _choose_candidate(d, exact, sig, addr_phone, locality_names)
        if not ev:
            continue
        loc = _norm(ev.get("locality"))
        if not loc:
            continue
        proposals.append((str(r[pk]), _norm(r.get(loc_col)), loc, ev, rule))
        m = re.search(r"(\d+)$", str(r[pk]))
        if m:
            anchored[int(m.group(1))] = loc

    with e.begin() as c:
        for ident, old, new, ev, rule in proposals:
            if old.casefold() == new.casefold():
                continue
            c.execute(text(
                f"UPDATE pi_magazine_master SET {_qid(loc_col)}=:v "
                f"WHERE CAST({_qid(pk)} AS TEXT)=:pk"
            ), {"v": new, "pk": ident})
            _audit(c, "pi_magazine_master", ident, loc_col, old, new, ev, rule)
            applied += 1

    # Reload after source-backed repairs, then fill only small interior holes between
    # identical proven locality anchors. This fixes cleaning gaps but never guesses a long block.
    with e.connect() as c:
        rows2 = [dict(r) for r in c.execute(text(
            "SELECT " + ",".join(_qid(x) for x in select_cols) + " FROM pi_magazine_master"
        )).mappings().all()]

    seq = {}
    for r in rows2:
        m = re.search(r"(\d+)$", str(r[pk]))
        if m:
            seq[int(m.group(1))] = r

    block_proposals = []
    for n, r in seq.items():
        old = _norm(r.get(loc_col))
        if old.upper() not in BAD:
            continue
        st = _norm(r.get(status_col)).upper() if status_col else ""
        if st in NON_PROPERTY or not _looks_like_property(r.get(desc_col) or ""):
            continue

        left = right = None
        for d in range(1, 9):
            z = seq.get(n - d)
            if z and _norm(z.get(loc_col)).upper() not in BAD and _looks_like_property(z.get(desc_col) or ""):
                left = (z, d)
                break
        for d in range(1, 9):
            z = seq.get(n + d)
            if z and _norm(z.get(loc_col)).upper() not in BAD and _looks_like_property(z.get(desc_col) or ""):
                right = (z, d)
                break
        if not left or not right:
            continue
        ll = _norm(left[0].get(loc_col))
        rr = _norm(right[0].get(loc_col))
        if not ll or ll.casefold() != rr.casefold():
            continue
        block_proposals.append((str(r[pk]), old, ll, str(left[0][pk]), str(right[0][pk])))

    with e.begin() as c:
        for ident, old, new, lp, rp in block_proposals:
            c.execute(text(
                f"UPDATE pi_magazine_master SET {_qid(loc_col)}=:v "
                f"WHERE CAST({_qid(pk)} AS TEXT)=:pk "
                f"AND UPPER(TRIM(COALESCE(CAST({_qid(loc_col)} AS TEXT),''))) "
                f"IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
            ), {"v": new, "pk": ident})
            _audit(c, "pi_magazine_master", ident, loc_col, old, new,
                   {"upload_id": None, "page_number": None, "original_text": f"anchors {lp} / {rp}"},
                   "SMALL_INTERIOR_BLOCK_BETWEEN_IDENTICAL_PROVEN_LOCALITY_ANCHORS")
            applied += 1

    return applied, {
        "status": "PASS",
        "source_backed_proposals": len(proposals),
        "small_block_proposals": len(block_proposals),
    }


def _patch_future_fastlane():
    try:
        import alliance_magazine_fastlane_v840 as fastlane
    except Exception:
        return False

    if getattr(fastlane, "_ALLIANCE_AI_DOCTOR_11920", False):
        return True

    def doctor_extract_candidates(page):
        parsed = _parse_page(page)
        chars = sum(len(x["text"]) for x in _native_lines(page))
        out = []
        for x in parsed:
            am = AREA_RE.search(x["original_text"])
            fl = FLOOR_RE.search(x["original_text"])
            pt = PROPERTY_TYPE_RE.search(x["original_text"])
            phones = list(_phone_set(x["original_text"]))
            raw_json = {
                "column": x["column"],
                "category_heading": x["section_heading"],
                "locality_heading": x["locality"],
                "hierarchy_version": VERSION,
                "text_chars_on_page": chars,
            }
            # section_heading intentionally carries locality for legacy downstream compatibility;
            # category is preserved separately in raw_json and transaction_type.
            out.append({
                "source_method": "NATIVE_PDF_TEXT_HIERARCHY",
                "section_heading": x["locality"],
                "original_description": x["original_text"],
                "transaction_type": (x["transaction"] or "").upper() if x["transaction"] else None,
                "property_type": pt.group(1).upper() if pt else None,
                "area_value": am.group(1) if am else None,
                "area_unit": _unit(am.group(2)) if am else None,
                "floor": fl.group(1).upper() if fl else None,
                "amount_raw": None,
                "contact_numbers": phones,
                "signal_score": 10 if (am or fl) and phones else 7,
                "needs_review": not bool(x["locality"]),
                "bbox": x["bbox"],
                "raw_json": raw_json,
            })
        return out, {"method": "NATIVE_PDF_TEXT_HIERARCHY", "text_chars": chars, "line_count": len(_native_lines(page))}

    fastlane._extract_candidates = doctor_extract_candidates
    fastlane._ALLIANCE_AI_DOCTOR_11920 = True
    return True


def _run(core):
    e = _engine(core)
    if e is None:
        return

    with LOCK:
        if STATE["status"] == "RUNNING":
            return
        STATE.update({
            "status": "RUNNING",
            "started_at": _utcnow(),
            "completed_at": None,
            "error": None,
            "details": {},
        })

    run_id = None
    try:
        _setup(e)
        with e.begin() as c:
            run_id = c.execute(text(f"""
                INSERT INTO {_qid(RUNS)}(version,status,started_at)
                VALUES(:v,'RUNNING',NOW()) RETURNING id
            """), {"v": VERSION}).scalar()

        STATE["future_parser_patched"] = _patch_future_fastlane()

        evidence_summary = _rebuild_evidence(e)
        STATE.update(evidence_summary)

        ev = _evidence_rows(e)
        indexes = _candidate_indexes(ev)

        complete_repairs = _repair_complete(e, indexes)
        master_repairs, master_details = _repair_master(e, indexes)

        STATE["complete_repairs"] = complete_repairs
        STATE["master_repairs"] = master_repairs
        STATE["details"] = {
            "master": master_details,
            "unique_localities_recovered": len({x["locality"] for x in ev if x.get("locality")}),
            "evidence_with_locality": len(ev),
            "safety": "No locality is invented. Ambiguous/unmatched rows stay unresolved.",
        }
        STATE["status"] = "PASS"
        STATE["completed_at"] = _utcnow()

        with e.begin() as c:
            c.execute(text(f"""
                UPDATE {_qid(RUNS)}
                SET status='PASS', completed_at=NOW(), summary=CAST(:s AS JSONB)
                WHERE id=:id
            """), {"id": run_id, "s": json.dumps(STATE, ensure_ascii=False)})

    except Exception as exc:
        STATE["status"] = "ERROR"
        STATE["completed_at"] = _utcnow()
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        STATE["details"] = {"trace": traceback.format_exc()[-6000:]}
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"""
                        UPDATE {_qid(RUNS)}
                        SET status='ERROR', completed_at=NOW(), summary=CAST(:s AS JSONB)
                        WHERE id=:id
                    """), {"id": run_id, "s": json.dumps(STATE, ensure_ascii=False)})
            except Exception:
                pass


def _start(core):
    t = threading.Thread(target=_run, args=(core,), daemon=True, name="alliance-magazine-ai-doctor-11920")
    t.start()


def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("Magazine AI Doctor requires app + engine")

    _setup(e)
    patched = _patch_future_fastlane()
    STATE["future_parser_patched"] = patched

    @app.get("/alliance/admin/magazine-doctor", response_class=HTMLResponse)
    def doctor_page(req: Request):
        _login(core, req)
        s = dict(STATE)
        body = f"""
        <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Magazine AI Doctor</title>
        <style>
        body{{font-family:Arial;margin:0;background:#f5f2ec;color:#27231e}}
        main{{max-width:1050px;margin:30px auto;background:white;padding:24px;border-radius:14px}}
        .card{{padding:14px;border:1px solid #ddd;border-radius:10px;margin:10px 0}}
        pre{{white-space:pre-wrap;background:#f7f7f7;padding:14px;border-radius:10px}}
        button{{padding:10px 16px;border:0;border-radius:8px;cursor:pointer}}
        </style></head><body><main>
        <h2>Alliance Magazine AI Doctor · 11.9.20</h2>
        <p>Whole-magazine hierarchy recovery: Section → Exact Locality → Property.</p>
        <div class="card"><b>Status:</b> {html.escape(str(s.get("status")))}</div>
        <div class="card"><b>Uploads:</b> {s.get("uploads",0)} · <b>Pages:</b> {s.get("pages",0)} ·
        <b>Evidence rows:</b> {s.get("evidence_rows",0)} · <b>Master repairs:</b> {s.get("master_repairs",0)}</div>
        <p><button onclick="run()">Run Full Repair Again</button></p>
        <pre id="o">{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <script>
        async function run(){{
          let r=await fetch('/api/alliance/admin/magazine-doctor/run',{{method:'POST'}});
          o.textContent=JSON.stringify(await r.json(),null,2);
          setTimeout(()=>location.reload(),2500);
        }}
        </script></main></body></html>
        """
        return HTMLResponse(body, headers={"Cache-Control": "no-store"})

    @app.get("/api/alliance/admin/magazine-doctor/status")
    def doctor_status(req: Request):
        _login(core, req)
        return JSONResponse(dict(STATE))

    @app.post("/api/alliance/admin/magazine-doctor/run")
    def doctor_run(req: Request):
        _login(core, req)
        if STATE["status"] == "RUNNING":
            return {"status": "ALREADY_RUNNING", "version": VERSION}
        _start(core)
        return {"status": "STARTED", "version": VERSION}

    # Automatic one-go repair after deployment.
    _start(core)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "future_parser_patched": patched,
        "admin_url": "/alliance/admin/magazine-doctor",
        "auto_repair": True,
    }
