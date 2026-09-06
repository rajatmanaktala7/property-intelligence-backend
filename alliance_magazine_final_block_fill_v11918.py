
from __future__ import annotations
import re, json
from sqlalchemy import text

VERSION = "11.9.18-MAGAZINE-FINAL-BLOCK-FILL"
MASTER = "pi_magazine_master"
AUDIT = "pi_magazine_final_block_audit_v11918"

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
EXCLUDE_STATUSES = {"EXCLUDE_NON_PROPERTY","ARCHIVED","REJECTED"}

def _engine(core): return getattr(core, "engine", None)

def _qid(s):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(s or "")):
        raise ValueError("unsafe identifier")
    return '"' + str(s) + '"'

def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def _exists(e, t):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar())

def _cols(e, t):
    with e.connect() as c:
        return [r[0] for r in c.execute(text("""
            SELECT column_name
            FROM information_schema.columns
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

def _is_property(desc, status):
    st=_norm(status).upper()
    if st in EXCLUDE_STATUSES:
        return False

    d=_norm(desc)
    u=d.upper()
    if not d:
        return False

    # Pure phone/contact/ad lines are not properties.
    stripped=re.sub(r"[\s,;:+()\-]","",u)
    if stripped.isdigit():
        return False
    if re.fullmatch(r"(?:\+?\d[\d\s,;/\-]{6,})", d):
        return False
    if "MOB." in u and not re.search(r"\b(?:GF|FF|SF|TF|BMT|LGF|UGF|MEZZ|\d+\s*(?:FT|Y|YD|SQFT|SQYD))\b",u):
        return False
    if "INTERIOR" in u or "COLLABORATION" in u or "CONSTRUCTIONS" in u:
        return False

    # Genuine property row should have a plausible address/plot token plus area/floor evidence.
    addr = bool(re.match(r"^\s*(?:[A-Z]{0,3}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", u))
    evidence = bool(re.search(r"\b(?:\d{2,7}\s*(?:FT|SQFT|Y|YD|SQYD|SQM|ACRE)|GF|FF|SF|TF|BMT|LGF|UGF|MEZZ)\b", u))
    return addr and evidence

def _setup(e):
    with e.begin() as c:
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(
                id BIGSERIAL PRIMARY KEY,
                master_pk TEXT NOT NULL,
                before_locality TEXT,
                after_locality TEXT NOT NULL,
                left_anchor_pk TEXT,
                right_anchor_pk TEXT,
                distance_left INTEGER,
                distance_right INTEGER,
                evidence_rule TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(master_pk, after_locality, version)
            )
        """))

def repair(e):
    if not e:
        return {"status":"SKIP","version":VERSION,"reason":"engine missing"}
    if not _exists(e, MASTER):
        return {"status":"SKIP","version":VERSION,"reason":f"{MASTER} missing"}

    cols=_cols(e,MASTER)
    pk=_pick(cols,"source_id","id","record_id","property_id")
    loc=_pick(cols,"locality","location")
    desc=_pick(cols,"original_raw_text","original_description","description")
    status=_pick(cols,"record_status","verification_status","status")
    category=_pick(cols,"category","property_category")
    tx=_pick(cols,"listing_type","transaction_type","transaction","type")

    if not all([pk,loc,desc]):
        return {"status":"SKIP","version":VERSION,"reason":"required master columns missing"}

    _setup(e)

    with e.connect() as c:
        rows=[r[0] for r in c.execute(text(f"SELECT to_jsonb(t) FROM {_qid(MASTER)} t")).all()]

    items=[]
    for r in rows:
        if not isinstance(r,dict):
            continue
        n=_suffix_int(r.get(pk))
        if n is None:
            continue
        items.append({
            "n":n,
            "pk":str(r.get(pk)),
            "loc":_norm(r.get(loc)),
            "desc":_norm(r.get(desc)),
            "status":_norm(r.get(status)) if status else "",
            "category":_norm(r.get(category)).upper() if category else "",
            "tx":_norm(r.get(tx)).upper() if tx else "",
        })

    items.sort(key=lambda x:x["n"])
    by_n={x["n"]:x for x in items}

    proposals=[]
    skipped_non_property=0
    skipped_unbounded=0
    skipped_conflict=0

    # Final pass rule:
    # Only fill MISSING rows that are genuine properties AND are enclosed
    # by the SAME known locality on both sides within 12 source rows.
    # Also require category/transaction compatibility when available.
    for x in items:
        if x["loc"].upper() not in BAD:
            continue
        if not _is_property(x["desc"], x["status"]):
            skipped_non_property+=1
            continue

        left=None
        right=None

        for d in range(1,13):
            y=by_n.get(x["n"]-d)
            if not y:
                continue
            if y["loc"].upper() not in BAD and _is_property(y["desc"],y["status"]):
                left=(y,d)
                break

        for d in range(1,13):
            y=by_n.get(x["n"]+d)
            if not y:
                continue
            if y["loc"].upper() not in BAD and _is_property(y["desc"],y["status"]):
                right=(y,d)
                break

        if not left or not right:
            skipped_unbounded+=1
            continue

        l,dl=left
        r,dr=right

        if l["loc"].casefold()!=r["loc"].casefold():
            skipped_conflict+=1
            continue

        # Do not bridge across a category / transaction boundary.
        if x["category"]:
            if l["category"] and l["category"] != x["category"]:
                skipped_conflict+=1
                continue
            if r["category"] and r["category"] != x["category"]:
                skipped_conflict+=1
                continue
        if x["tx"]:
            if l["tx"] and l["tx"] != x["tx"]:
                skipped_conflict+=1
                continue
            if r["tx"] and r["tx"] != x["tx"]:
                skipped_conflict+=1
                continue

        # Refuse to bridge over another KNOWN conflicting locality between anchors.
        conflict=False
        for n in range(l["n"]+1, r["n"]):
            z=by_n.get(n)
            if z and z["loc"].upper() not in BAD and z["loc"].casefold()!=l["loc"].casefold():
                conflict=True
                break
        if conflict:
            skipped_conflict+=1
            continue

        proposals.append({
            "pk":x["pk"],
            "before":x["loc"],
            "after":l["loc"],
            "left_pk":l["pk"],
            "right_pk":r["pk"],
            "dl":dl,
            "dr":dr,
        })

    applied=0
    with e.begin() as c:
        for p in proposals:
            r=c.execute(text(
                f"UPDATE {_qid(MASTER)} SET {_qid(loc)}=:v "
                f"WHERE CAST({_qid(pk)} AS TEXT)=:pk "
                f"AND UPPER(TRIM(COALESCE(CAST({_qid(loc)} AS TEXT),''))) "
                f"IN ('','MISSING','UNKNOWN','N/A','NA','NONE','NULL','UNSPECIFIED')"
            ),{"v":p["after"],"pk":p["pk"]})
            if not r.rowcount:
                continue
            applied+=1
            c.execute(text(f"""
                INSERT INTO {_qid(AUDIT)}
                (master_pk,before_locality,after_locality,left_anchor_pk,right_anchor_pk,
                 distance_left,distance_right,evidence_rule,version)
                VALUES(:pk,:b,:a,:lp,:rp,:dl,:dr,:rule,:ver)
                ON CONFLICT DO NOTHING
            """),{
                "pk":p["pk"],"b":p["before"],"a":p["after"],
                "lp":p["left_pk"],"rp":p["right_pk"],"dl":p["dl"],"dr":p["dr"],
                "rule":"GENUINE_PROPERTY + SAME_LOCALITY_LEFT_RIGHT_ANCHORS_WITHIN_12 + NO_CONFLICTING_BOUNDARY",
                "ver":VERSION
            })

    return {
        "status":"PASS",
        "version":VERSION,
        "rows_scanned":len(items),
        "proposed":len(proposals),
        "applied":applied,
        "skipped_non_property":skipped_non_property,
        "skipped_unbounded":skipped_unbounded,
        "skipped_conflicting_boundary":skipped_conflict,
        "duplicates_created":0,
        "safety":"fills only currently missing genuine property rows bounded by the same proven locality"
    }

def register(core):
    try:
        return repair(_engine(core))
    except Exception as exc:
        return {"status":"ERROR","version":VERSION,"error":f"{type(exc).__name__}: {exc}","fail_safe":True}
