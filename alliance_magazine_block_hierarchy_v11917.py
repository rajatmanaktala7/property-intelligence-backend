
from __future__ import annotations
import re, json
from collections import Counter, defaultdict
from sqlalchemy import text

VERSION = "11.9.17-MAGAZINE-BLOCK-HIERARCHY-REPAIR"
MASTER = "pi_magazine_master"
UPSTREAM = "pi_magazine_complete_v860"
AUDIT = "pi_magazine_block_audit_v11917"

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}

def _engine(core): return getattr(core, "engine", None)

def _qid(s):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(s or "")):
        raise ValueError("unsafe identifier")
    return '"' + str(s) + '"'

def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def _norm_key(s):
    s = _norm(s).upper()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b", "", s)
    s = re.sub(r"\b0\d{2,4}[-\s]?\d{6,8}\b", "", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _addr_token(s):
    s = _norm(s).upper()
    m = re.match(r"^\s*([A-Z]{0,3}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", s)
    return m.group(1).replace(" ", "") if m else ""

def _area_floor_signature(s):
    u = _norm(s).upper()
    area = ""
    m = re.search(r"\b(\d{2,7})\s*(FT|SQFT|Y|YD|SQYD|SQM|ACRE)\b", u)
    if m:
        area = m.group(1) + m.group(2)
    floors = []
    for f in ("BMT","LGF","UGF","GF","FF","SF","TF","MEZZ"):
        if re.search(rf"\b{f}\b", u):
            floors.append(f)
    return area, tuple(floors)

def _canonical_locality(raw):
    s = _norm(raw)
    if not s:
        return ""
    u = s.upper().strip(" -:|")

    m = re.fullmatch(r"OKHLA(?:\s+INDUSTRIAL\s+AREA)?\s*(?:PHASE|PH\.?)?\s*[- ]?\s*(I{1,3}|[123])", u)
    if m:
        p = {"I":"1","II":"2","III":"3"}.get(m.group(1), m.group(1))
        return f"Okhla Phase {p}"

    # Reject section/category labels and obvious non-locality text.
    if re.fullmatch(r"(?:RESIDENTIAL|COMMERCIAL|INDUSTRIAL|RETAIL|OFFICE|HOSPITALITY|FARMHOUSE)(?:\s*[- ]\s*(?:SALE|RENT|LEASE))?", u):
        return ""
    if re.fullmatch(r"(?:SALE|RENT|LEASE|BUY|SELL|RENTING)", u):
        return ""
    if len(u) > 70:
        return ""
    if re.search(r"\b[6-9]\d{9}\b", u):
        return ""
    if re.search(r"\b\d{2,7}\s*(?:FT|SQFT|Y|YD|SQYD|SQM|ACRE)\b", u):
        return ""
    if "PAGE-" in u or "CONSTRUCTION" in u or "INTERIOR" in u or "COLLABORATION" in u:
        return ""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 .&'()/\-]{1,68}", u):
        return ""

    u = re.sub(r"\s*-\s*$", "", u)
    u = re.sub(r"\s+", " ", u).strip()
    return " ".join(w if w in {"GK","DLF","NCR"} else w.title() for w in u.split())

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
    low={x.lower():x for x in cols}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None

def _suffix_int(v):
    m=re.search(r"(\d+)$", str(v or ""))
    return int(m.group(1)) if m else None

def _setup(e):
    with e.begin() as c:
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(
                id BIGSERIAL PRIMARY KEY,
                master_pk TEXT NOT NULL,
                before_locality TEXT,
                after_locality TEXT NOT NULL,
                upstream_id TEXT NOT NULL,
                upstream_section TEXT NOT NULL,
                offset_used INTEGER,
                anchor_support INTEGER NOT NULL,
                evidence_rule TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(master_pk, after_locality, version)
            )
        """))

def repair(e):
    if not e:
        return {"status":"SKIP","version":VERSION,"reason":"engine missing"}
    if not _exists(e, MASTER) or not _exists(e, UPSTREAM):
        return {"status":"SKIP","version":VERSION,"reason":"required table missing"}

    mcols=_cols(e,MASTER); ucols=_cols(e,UPSTREAM)
    mpk=_pick(mcols,"source_id","id","record_id","property_id")
    mdesc=_pick(mcols,"original_raw_text","original_description","description")
    mloc=_pick(mcols,"locality","location")
    mcat=_pick(mcols,"category","property_category")

    uid=_pick(ucols,"id")
    urec=_pick(ucols,"source_record_id","record_id","property_id","id")
    udesc=_pick(ucols,"description","original_description")
    uorig=_pick(ucols,"original_description","description")
    usection=_pick(ucols,"original_section","section_heading","locality_heading","location_heading")
    ucat=_pick(ucols,"property_category","category")
    upage=_pick(ucols,"page_number","page_no","page")

    if not all([mpk,mdesc,mloc,uid,urec,udesc,usection]):
        return {"status":"SKIP","version":VERSION,"reason":"required columns missing"}

    _setup(e)

    with e.connect() as c:
        masters=[r[0] for r in c.execute(text(f"SELECT to_jsonb(t) FROM {_qid(MASTER)} t")).all()]
        ups=[r[0] for r in c.execute(text(f"SELECT to_jsonb(t) FROM {_qid(UPSTREAM)} t")).all()]

    # Build exact normalized-description anchors.
    up_by_key=defaultdict(list)
    up_by_id={}
    for u in ups:
        if not isinstance(u,dict): continue
        try:
            up_by_id[int(u.get(uid))]=u
        except Exception:
            pass
        for val in (u.get(udesc), u.get(uorig) if uorig else None):
            k=_norm_key(val)
            if k:
                up_by_key[k].append(u)

    anchor_offsets=[]
    anchor_pairs=[]
    for m in masters:
        if not isinstance(m,dict): continue
        msuf=_suffix_int(m.get(mpk))
        if msuf is None: continue
        cand=up_by_key.get(_norm_key(m.get(mdesc)),[])
        # Anchor only when exact normalized description points to one upstream row.
        if len(cand)!=1: continue
        try:
            ui=int(cand[0].get(uid))
        except Exception:
            continue
        loc=_canonical_locality(cand[0].get(usection))
        if not loc: continue
        off=ui-msuf
        anchor_offsets.append(off)
        anchor_pairs.append((msuf,ui,off,loc))

    counts=Counter(anchor_offsets)
    # Strong offsets only. Five anchors avoids one-off coincidences.
    strong={off:n for off,n in counts.items() if n>=5}
    if not strong:
        return {
            "status":"SKIP","version":VERSION,
            "reason":"no sequence offset has >=5 exact anchors",
            "anchor_offsets":dict(counts)
        }

    # Index anchors by master suffix for local support checks.
    anchors_by_suffix=defaultdict(list)
    for msuf,ui,off,loc in anchor_pairs:
        if off in strong:
            anchors_by_suffix[msuf].append((off,loc))

    proposals=[]
    skipped_no_support=0
    skipped_mismatch=0
    skipped_ambiguous=0

    for m in masters:
        if not isinstance(m,dict): continue
        old=_norm(m.get(mloc))
        if old.upper() not in BAD:
            continue
        msuf=_suffix_int(m.get(mpk))
        if msuf is None:
            continue

        # Candidate offsets must be supported by nearby exact anchors.
        candidate_offsets=[]
        for off,total_support in strong.items():
            local_support=0
            for dist in range(1,26):
                for pos in (msuf-dist, msuf+dist):
                    for aoff,_ in anchors_by_suffix.get(pos,[]):
                        if aoff==off:
                            local_support+=1
                if local_support>=2:
                    break
            if local_support>=2:
                candidate_offsets.append((off,total_support,local_support))

        if not candidate_offsets:
            skipped_no_support+=1
            continue

        candidate_offsets.sort(key=lambda x:(x[2],x[1]),reverse=True)
        best=candidate_offsets[0]
        if len(candidate_offsets)>1 and candidate_offsets[1][2]==best[2] and candidate_offsets[1][1]==best[1]:
            skipped_ambiguous+=1
            continue

        off,total_support,local_support=best
        u=up_by_id.get(msuf+off)
        if not u:
            skipped_no_support+=1
            continue

        loc=_canonical_locality(u.get(usection))
        if not loc:
            skipped_no_support+=1
            continue

        # Row-level identity check. We are not relying only on sequence position:
        # address token must match, or area+floor signature must match strongly.
        md=_norm(m.get(mdesc)); ud=_norm(u.get(uorig) if uorig else u.get(udesc))
        ma=_addr_token(md); ua=_addr_token(ud)
        msig=_area_floor_signature(md); usig=_area_floor_signature(ud)

        token_ok=bool(ma and ua and ma==ua)
        sig_ok=bool(msig[0] and usig[0] and msig==usig)
        if not (token_ok or sig_ok):
            skipped_mismatch+=1
            continue

        # Category consistency when both sides provide it.
        if mcat and ucat:
            mc=_norm(m.get(mcat)).upper()
            uc=_norm(u.get(ucat)).upper()
            if mc and uc and mc not in BAD and uc not in BAD and mc!=uc:
                skipped_mismatch+=1
                continue

        proposals.append({
            "pk":str(m.get(mpk)),
            "before":old,
            "after":loc,
            "upstream_id":str(u.get(urec)),
            "section":_norm(u.get(usection)),
            "offset":off,
            "support":total_support,
            "page":u.get(upage) if upage else None,
        })

    applied=0
    with e.begin() as c:
        for p in proposals:
            r=c.execute(text(
                f"UPDATE {_qid(MASTER)} SET {_qid(mloc)}=:loc "
                f"WHERE CAST({_qid(mpk)} AS TEXT)=:pk "
                f"AND UPPER(TRIM(COALESCE(CAST({_qid(mloc)} AS TEXT),''))) "
                f"IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
            ),{"loc":p["after"],"pk":p["pk"]})
            if not r.rowcount:
                continue
            applied+=1
            c.execute(text(f"""
                INSERT INTO {_qid(AUDIT)}
                (master_pk,before_locality,after_locality,upstream_id,upstream_section,
                 offset_used,anchor_support,evidence_rule,version)
                VALUES(:pk,:b,:a,:uid,:sec,:off,:sup,:rule,:ver)
                ON CONFLICT DO NOTHING
            """),{
                "pk":p["pk"],"b":p["before"],"a":p["after"],
                "uid":p["upstream_id"],"sec":p["section"],
                "off":p["offset"],"sup":p["support"],
                "rule":"LOCAL SEQUENCE BLOCK + >=5 GLOBAL EXACT ANCHORS + ROW IDENTITY TOKEN/SIGNATURE",
                "ver":VERSION
            })

    return {
        "status":"PASS",
        "version":VERSION,
        "master_rows_scanned":len(masters),
        "upstream_rows_scanned":len(ups),
        "strong_offsets":strong,
        "proposed":len(proposals),
        "applied":applied,
        "skipped_no_local_anchor_support":skipped_no_support,
        "skipped_row_identity_mismatch":skipped_mismatch,
        "skipped_ambiguous_offset":skipped_ambiguous,
        "duplicates_created":0,
        "safety":"only missing/unknown locality rows updated; ambiguous rows unchanged"
    }

def register(core):
    try:
        return repair(_engine(core))
    except Exception as exc:
        return {"status":"ERROR","version":VERSION,"error":f"{type(exc).__name__}: {exc}","fail_safe":True}
