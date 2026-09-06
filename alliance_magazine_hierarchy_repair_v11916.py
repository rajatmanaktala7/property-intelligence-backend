
from __future__ import annotations
import re, json
from sqlalchemy import text

VERSION = "11.9.16-FINAL-MAGAZINE-HIERARCHY-REPAIR"
MASTER = "pi_magazine_master"
UPSTREAM = "pi_magazine_complete_v860"
AUDIT = "pi_magazine_hierarchy_audit_v11916"

BAD_VALUES = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}

SECTION_WORDS = {
    "RESIDENTIAL","COMMERCIAL","INDUSTRIAL","RETAIL","OFFICE","HOSPITALITY",
    "FARMHOUSE","SALE","RENT","LEASE","RENTING","SELL","BUY"
}

def _engine(core): return getattr(core, "engine", None)

def _qid(s):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(s or "")):
        raise ValueError("unsafe identifier")
    return '"' + str(s) + '"'

def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def _norm_key(s):
    s = _norm(s).upper()
    s = re.sub(r"\([^)]*\)", "", s)  # drop contacts/remarks in brackets
    s = re.sub(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b", "", s)
    s = re.sub(r"\b0\d{2,4}[-\s]?\d{6,8}\b", "", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _exists(e, t):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar())

def _cols(e, t):
    with e.connect() as c:
        return [r[0] for r in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=:t
            ORDER BY ordinal_position
        """), {"t": t}).all()]

def _pick(cols, *names):
    low = {x.lower(): x for x in cols}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None

def _bad(v):
    return _norm(v).upper() in BAD_VALUES

def _canonical_locality(raw):
    s = _norm(raw)
    if not s:
        return ""

    u = s.upper().strip(" -:|")

    # Exact Okhla phase preservation.
    m = re.fullmatch(r"OKHLA(?:\s+INDUSTRIAL\s+AREA)?\s*(?:PHASE|PH\.?)?\s*[- ]?\s*(I{1,3}|[123])", u)
    if m:
        p = {"I":"1","II":"2","III":"3"}.get(m.group(1), m.group(1))
        return f"Okhla Phase {p}"

    # Never treat transaction/category headings as localities.
    tokens = set(re.findall(r"[A-Z]+", u))
    if tokens and tokens.issubset(SECTION_WORDS):
        return ""
    if re.search(r"\b(?:RESIDENTIAL|COMMERCIAL|INDUSTRIAL|RETAIL|OFFICE|HOSPITALITY|FARMHOUSE)\b.*\b(?:SALE|RENT|LEASE|RENTING)\b", u):
        return ""

    # Reject obvious property rows, contact rows, dates and ad text.
    if len(u) > 70:
        return ""
    if re.search(r"\b[6-9]\d{9}\b", u):
        return ""
    if re.search(r"\b\d{2,7}\s*(?:FT|SQFT|Y|YD|SQYD|SQM|ACRE)\b", u):
        return ""
    if re.search(r"\b(?:GF|FF|SF|TF|BMT|BASEMENT|LGF|MEZZ)\b", u) and re.search(r"\d", u):
        return ""
    if re.search(r"\bSEP[- ]?20\d{2}\b|\b20\d{2}\b", u):
        return ""
    if "PAGE-" in u or "CONSTRUCTION" in u or "INTERIOR" in u or "COLLABORATION" in u:
        return ""

    # A locality heading can contain digits (e.g. GK-1, Sector 18, Okhla-3).
    # It should still be short and heading-like.
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 .&'()/\-]{1,68}", u):
        return ""

    # Normalize common punctuation while preserving specificity.
    u = re.sub(r"\s*-\s*$", "", u)
    u = re.sub(r"\s+", " ", u).strip()

    # Friendly casing with protected acronyms.
    words=[]
    for w in u.split():
        if w in {"GK","DLF","NCR"}:
            words.append(w)
        elif re.fullmatch(r"[A-Z]+-\d+", w):
            a,b=w.rsplit("-",1)
            words.append(a.title()+"-"+b)
        else:
            words.append(w.title())
    return " ".join(words)

def _setup_audit(e):
    with e.begin() as c:
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(
                id BIGSERIAL PRIMARY KEY,
                master_pk TEXT NOT NULL,
                before_locality TEXT,
                after_locality TEXT NOT NULL,
                upstream_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                upstream_sections JSONB NOT NULL DEFAULT '[]'::jsonb,
                evidence_rule TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(master_pk, after_locality, version)
            )
        """))

def repair(e):
    if not e:
        return {"status":"SKIP","reason":"engine missing","version":VERSION}
    if not _exists(e, MASTER):
        return {"status":"SKIP","reason":f"{MASTER} missing","version":VERSION}
    if not _exists(e, UPSTREAM):
        return {"status":"SKIP","reason":f"{UPSTREAM} missing","version":VERSION}

    mcols = _cols(e, MASTER)
    ucols = _cols(e, UPSTREAM)

    mpk = _pick(mcols, "source_id","id","record_id","property_id","magazine_id","row_id")
    mdesc = _pick(mcols, "original_raw_text","original_description","description","property_description")
    mloc = _pick(mcols, "locality","location","locality_clean")
    mcat = _pick(mcols, "category","property_category")
    mtx = _pick(mcols, "listing_type","transaction_type","transaction","rent_or_sale","deal_type")

    uid = _pick(ucols, "source_record_id","record_id","property_id","id")
    udesc = _pick(ucols, "description","original_description","clean_description")
    uorig = _pick(ucols, "original_description","description")
    usection = _pick(ucols, "original_section","section_heading","locality_heading","location_heading")
    ucat = _pick(ucols, "property_category","category")
    utx = _pick(ucols, "transaction_type","listing_type","transaction")

    if not all([mpk, mdesc, mloc, uid, udesc, usection]):
        return {
            "status":"SKIP","version":VERSION,
            "reason":"required columns missing",
            "master":{"pk":mpk,"desc":mdesc,"loc":mloc},
            "upstream":{"id":uid,"desc":udesc,"section":usection}
        }

    _setup_audit(e)

    with e.connect() as c:
        masters = [r[0] for r in c.execute(text(
            f"SELECT to_jsonb(t) d FROM {_qid(MASTER)} t"
        )).all()]
        ups = [r[0] for r in c.execute(text(
            f"SELECT to_jsonb(t) d FROM {_qid(UPSTREAM)} t"
        )).all()]

    # Build normalized description -> upstream candidates.
    by_key = {}
    for d in ups:
        if not isinstance(d, dict):
            continue
        keys = {_norm_key(d.get(udesc))}
        if uorig:
            keys.add(_norm_key(d.get(uorig)))
        for k in keys:
            if k:
                by_key.setdefault(k, []).append(d)

    proposals=[]
    ambiguous=0
    no_match=0
    no_heading=0

    for m in masters:
        if not isinstance(m, dict):
            continue
        mk = _norm_key(m.get(mdesc))
        if not mk:
            no_match += 1
            continue

        cand = by_key.get(mk, [])
        if not cand:
            no_match += 1
            continue

        # Strong evidence: all exact-description candidates must resolve to one
        # and only one canonical locality.
        localities={}
        for d in cand:
            loc = _canonical_locality(d.get(usection))
            if loc:
                localities.setdefault(loc, []).append(d)

        if not localities:
            no_heading += 1
            continue
        if len(localities) != 1:
            ambiguous += 1
            continue

        newloc, winning = next(iter(localities.items()))
        oldloc = _norm(m.get(mloc))

        # Correct missing localities AND stale/wrong localities when exact
        # upstream description gives a single consistent parent heading.
        if oldloc.casefold() == newloc.casefold():
            continue

        proposals.append({
            "pk": str(m.get(mpk)),
            "before": oldloc,
            "after": newloc,
            "upstream_ids": [str(x.get(uid)) for x in winning],
            "sections": sorted({_norm(x.get(usection)) for x in winning if _norm(x.get(usection))}),
            "category": _norm(winning[0].get(ucat)) if ucat and winning else "",
            "transaction": _norm(winning[0].get(utx)) if utx and winning else "",
        })

    applied=0
    cat_filled=0
    tx_filled=0

    with e.begin() as c:
        for p in proposals:
            r = c.execute(text(
                f"UPDATE {_qid(MASTER)} SET {_qid(mloc)}=:loc "
                f"WHERE CAST({_qid(mpk)} AS TEXT)=:pk"
            ), {"loc":p["after"],"pk":p["pk"]})
            if not r.rowcount:
                continue

            applied += 1

            # Fill category / transaction only when legacy value is blank-like.
            if mcat and p["category"]:
                rr = c.execute(text(
                    f"UPDATE {_qid(MASTER)} SET {_qid(mcat)}=:v "
                    f"WHERE CAST({_qid(mpk)} AS TEXT)=:pk "
                    f"AND UPPER(TRIM(COALESCE(CAST({_qid(mcat)} AS TEXT),''))) "
                    f"IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
                ), {"v":p["category"],"pk":p["pk"]})
                cat_filled += max(0, rr.rowcount or 0)

            if mtx and p["transaction"]:
                rr = c.execute(text(
                    f"UPDATE {_qid(MASTER)} SET {_qid(mtx)}=:v "
                    f"WHERE CAST({_qid(mpk)} AS TEXT)=:pk "
                    f"AND UPPER(TRIM(COALESCE(CAST({_qid(mtx)} AS TEXT),''))) "
                    f"IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
                ), {"v":p["transaction"],"pk":p["pk"]})
                tx_filled += max(0, rr.rowcount or 0)

            c.execute(text(f"""
                INSERT INTO {_qid(AUDIT)}
                (master_pk,before_locality,after_locality,upstream_ids,upstream_sections,evidence_rule,version)
                VALUES(:pk,:b,:a,CAST(:ids AS JSONB),CAST(:secs AS JSONB),:rule,:ver)
                ON CONFLICT DO NOTHING
            """), {
                "pk":p["pk"],"b":p["before"],"a":p["after"],
                "ids":json.dumps(p["upstream_ids"]),
                "secs":json.dumps(p["sections"]),
                "rule":"EXACT_NORMALIZED_DESCRIPTION + SINGLE_CONSISTENT_PARENT_HEADING",
                "ver":VERSION
            })

    return {
        "status":"PASS",
        "version":VERSION,
        "master_rows_scanned":len(masters),
        "upstream_rows_scanned":len(ups),
        "proposed":len(proposals),
        "applied_locality_repairs":applied,
        "category_filled_when_blank":cat_filled,
        "transaction_filled_when_blank":tx_filled,
        "ambiguous_left_unchanged":ambiguous,
        "no_upstream_match_left_unchanged":no_match,
        "no_trusted_heading_left_unchanged":no_heading,
        "duplicates_created":0,
        "rule":"SECTION/LOCALITY HEADING -> EXACT PROPERTY ROW",
        "okhla":"OKHLA-1/2/3 => Okhla Phase 1/2/3",
        "safety":"ambiguous or unsupported rows remain unchanged"
    }

def register(core):
    try:
        return repair(_engine(core))
    except Exception as exc:
        return {
            "status":"ERROR","version":VERSION,
            "error":f"{type(exc).__name__}: {exc}",
            "fail_safe":True
        }
