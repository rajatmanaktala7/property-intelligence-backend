
from __future__ import annotations

import html
import json
import re
import threading
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.0.4.3-CERTIFICATION-FULL-VIEW-REBUILD-FIX"
STAGE = "pi_magazine_golden_stage_v12003"
CERT = "pi_magazine_certification_v12004"
DUPS = "pi_magazine_duplicate_map_v12004"
RUNS = "pi_magazine_certification_runs_v12004"
LOCK_KEY = 120040001

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
ORG_RE = re.compile(r"(?i)\b(?:CONSTRUCTION|CONSTRUCTIONS|BUILDER|BUILDERS|DEVELOPER|DEVELOPERS|REALTOR|REALTORS|REALTY|ESTATE|ESTATES|PROPERTIES|PROPERTY\s+DEALER|INFRA|INFRASTRUCTURE|ASSOCIATES|CONSULTANTS|CONSULTANCY|PVT|LTD|LLP|ENTERPRISES|CORPORATION|COMPANY|CO\.?|GROUP|INTERIORS|ARCHITECTS)\b")
PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
ADDRESS_RE = re.compile(r"^\s*([A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?/[0-9A-Z/-]+|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b", re.I)

LOCK = threading.Lock()
STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_processed":0,"gold_seeded":0,"pending_review":0,
    "human_approved":0,"human_rejected":0,"duplicate_groups":0,"duplicate_rows":0,
    "certified_unique":0,"ai_training_rows":0,"error":None,"details":{}
}

def _now(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _key(v):
    s = _norm(v).upper()
    s = PHONE_RE.sub(" ", s)
    s = re.sub(r"[^A-Z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _valid_location(v):
    s=_norm(v)
    if not s or s.upper() in BAD: return False
    if len(s)>75: return False
    if ORG_RE.search(s) or PHONE_RE.search(s) or AREA_RE.search(s): return False
    return True

def _addr(desc):
    m=ADDRESS_RE.search(_norm(desc))
    return _key(m.group(1)) if m else ""

def _area(desc):
    m=AREA_RE.search(_norm(desc))
    if not m: return ""
    value=m.group(1)
    unit=re.sub(r"[^A-Z]","",m.group(2).upper())
    aliases={"FT":"SQFT","SQFT":"SQFT","Y":"SQYD","YD":"SQYD","SQYD":"SQYD","SQM":"SQM","ACRE":"ACRE"}
    return f"{value}:{aliases.get(unit,unit)}"

def _floor(desc):
    m=FLOOR_RE.search(_norm(desc))
    if not m: return ""
    u=_key(m.group(1))
    aliases={
        "BASEMENT":"BMT","GROUND FLOOR":"GF","FIRST FLOOR":"FF",
        "SECOND FLOOR":"SF","THIRD FLOOR":"TF"
    }
    return aliases.get(u,u)

def _phone(v):
    vals=PHONE_RE.findall(_norm(v))
    return vals[0] if vals else ""

def _transaction(row):
    for k in ("listing_type","transaction_type","category","property_type"):
        v=_norm(row.get(k))
        if v:
            u=v.upper()
            if "RENT" in u or "LEASE" in u: return "RENT"
            if "SALE" in u or "SELL" in u: return "SALE"
    return ""

def _setup(e):
    with e.begin() as c:
        if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":STAGE}).scalar():
            raise RuntimeError("12.0.3 stage table is missing. Run Golden Data 12.0.3 first.")
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {CERT}(
          source_id TEXT PRIMARY KEY,
          decision TEXT NOT NULL DEFAULT 'PENDING',
          certified_location TEXT,
          reviewer TEXT,
          review_note TEXT,
          decision_source TEXT NOT NULL DEFAULT 'SYSTEM',
          decided_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {DUPS}(
          source_id TEXT PRIMARY KEY,
          duplicate_group TEXT,
          duplicate_rank INTEGER,
          duplicate_confidence INTEGER,
          fingerprint TEXT,
          reason TEXT,
          survivor_source_id TEXT,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {RUNS}(
          id BIGSERIAL PRIMARY KEY,
          version TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,
          summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )"""))

def _master_columns(e):
    with e.connect() as c:
        cols=[r[0] for r in c.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='pi_magazine_master'
        ORDER BY ordinal_position
        """)).all()]
    low={x.lower():x for x in cols}
    return {
        "source_id":low.get("source_id"),
        "desc":low.get("original_raw_text") or low.get("original_description") or low.get("description"),
        "locality":low.get("locality") or low.get("location"),
        "phone":low.get("valid_mobiles") or low.get("contact_number") or low.get("contact_numbers"),
        "listing_type":low.get("listing_type"),
        "transaction_type":low.get("transaction_type"),
        "category":low.get("category"),
        "property_type":low.get("property_type"),
        "area":low.get("area"),
        "area_unit":low.get("area_unit"),
        "floor":low.get("floor"),
        "price":low.get("price"),
        "contact_name":low.get("contact_name_company") or low.get("contact_name"),
    }

def _select_rows(e):
    m=_master_columns(e)
    if not m["source_id"] or not m["desc"]:
        raise RuntimeError("pi_magazine_master source_id/description columns missing")
    optional=[k for k in ("phone","listing_type","transaction_type","category","property_type","area","area_unit","floor","price","contact_name") if m.get(k)]
    sel=[
        f'CAST(x."{m["source_id"]}" AS TEXT) AS source_id',
        f'COALESCE(x."{m["desc"]}",\'\') AS original_raw_text',
        "g.canonical_location",
        "g.location_confidence",
        "g.location_rule",
        "g.quality_status",
        "g.quality_score",
        "g.conflict",
    ]
    for k in optional:
        sel.append(f'COALESCE(CAST(x."{m[k]}" AS TEXT),\'\') AS "{k}"')
    sql=f"""
    SELECT {",".join(sel)}
    FROM pi_magazine_master x
    JOIN {STAGE} g ON g.source_id=CAST(x."{m["source_id"]}" AS TEXT)
    WHERE g.version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
    ORDER BY x."{m["source_id"]}"
    """
    with e.connect() as c:
        return [dict(r) for r in c.execute(text(sql)).mappings().all()]

def _duplicate_key(row):
    loc=_key(row.get("canonical_location"))
    desc=_norm(row.get("original_raw_text"))
    addr=_addr(desc)
    area=_area(desc) or _key(f'{row.get("area","")} {row.get("area_unit","")}')
    floor=_floor(desc) or _key(row.get("floor"))
    txn=_transaction(row)
    phone=_phone(row.get("phone",""))

    # Strong survivorship key. Price is deliberately excluded because the same
    # property may be advertised repeatedly at changed prices.
    if loc and addr and area and floor and txn:
        return ("PROPERTY5",loc,addr,area,floor,txn), 96
    # Exact ad + same phone is also a strong duplicate signal.
    exact=_key(desc)
    if exact and phone:
        return ("TEXTPHONE",exact,phone), 94
    return None,0

def _survivor_score(row):
    q=row.get("quality_status")
    base={"GOLD":1000,"SILVER":700,"REVIEW":300,"QUARANTINED":100}.get(q,0)
    base += int(row.get("quality_score") or 0)
    base += int(row.get("location_confidence") or 0)
    if _phone(row.get("phone","")): base+=20
    if _norm(row.get("contact_name")): base+=10
    if _norm(row.get("price")): base+=5
    return base

def _build(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"ACQUIRING_LOCK","started_at":_now(),"completed_at":None,
                      "rows_total":0,"rows_processed":0,"gold_seeded":0,"pending_review":0,
                      "human_approved":0,"human_rejected":0,"duplicate_groups":0,"duplicate_rows":0,
                      "certified_unique":0,"ai_training_rows":0,"error":None,"details":{}})
    lock_conn=None
    run_id=None
    try:
        _setup(e)
        lock_conn=e.connect()
        got=bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar())
        if not got:
            STATE["status"]="SKIPPED"; STATE["phase"]="ANOTHER_CERTIFICATION_BUILD_RUNNING"
            STATE["completed_at"]=_now()
            return

        STATE["phase"]="LOADING_GOVERNED_ROWS"
        rows=_select_rows(e)
        STATE["rows_total"]=len(rows)

        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()

        # Seed AUTO_GOLD while preserving all human decisions across rebuilds.
        gold_seeded=0
        pending=0
        with e.begin() as c:
            for r in rows:
                sid=r["source_id"]; q=r["quality_status"]
                existing=c.execute(text(f"SELECT decision FROM {CERT} WHERE source_id=:sid"),{"sid":sid}).scalar()
                if existing in ("HUMAN_APPROVED","HUMAN_REJECTED"):
                    continue
                if q=="GOLD" and not bool(r.get("conflict")):
                    c.execute(text(f"""
                    INSERT INTO {CERT}(source_id,decision,certified_location,decision_source,decided_at,updated_at)
                    VALUES(:sid,'AUTO_GOLD',:loc,'12.0.3',NOW(),NOW())
                    ON CONFLICT(source_id) DO UPDATE SET
                      decision='AUTO_GOLD',certified_location=EXCLUDED.certified_location,
                      decision_source='12.0.3',decided_at=NOW(),updated_at=NOW()
                    """),{"sid":sid,"loc":r.get("canonical_location")})
                    gold_seeded+=1
                else:
                    c.execute(text(f"""
                    INSERT INTO {CERT}(source_id,decision,certified_location,decision_source,updated_at)
                    VALUES(:sid,'PENDING',:loc,'12.0.3',NOW())
                    ON CONFLICT(source_id) DO NOTHING
                    """),{"sid":sid,"loc":r.get("canonical_location")})
                    pending+=1

        STATE["phase"]="DETECTING_DUPLICATES"
        groups=defaultdict(list)
        for r in rows:
            key,conf=_duplicate_key(r)
            if key:
                groups[key].append((r,conf))
        dup_groups=[(k,v) for k,v in groups.items() if len(v)>1]

        with e.begin() as c:
            c.execute(text(f"DELETE FROM {DUPS}"))
            for i,(finger,vals) in enumerate(dup_groups,1):
                gid=f"MAG-DUP-{i:05d}"
                ranked=sorted(vals,key=lambda x:(_survivor_score(x[0]),x[0]["source_id"]),reverse=True)
                survivor=ranked[0][0]["source_id"]
                for rank,(r,conf) in enumerate(ranked,1):
                    c.execute(text(f"""
                    INSERT INTO {DUPS}
                    (source_id,duplicate_group,duplicate_rank,duplicate_confidence,fingerprint,reason,survivor_source_id,updated_at)
                    VALUES(:sid,:g,:r,:c,:f,:why,:s,NOW())
                    """),{
                        "sid":r["source_id"],"g":gid,"r":rank,"c":conf,
                        "f":"|".join(map(str,finger)),
                        "why":"Same governed locality + address + area + floor + transaction" if finger[0]=="PROPERTY5" else "Exact ad text + same phone",
                        "s":survivor
                    })

        STATE["phase"]="BUILDING_CERTIFIED_VIEWS"
        with e.begin() as c:
            # Rebuild dependency-safe: child views first, then parent.
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12004"))
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12004"))
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_certified_master_v12004"))

            c.execute(text(f"""
            CREATE VIEW pi_magazine_certified_master_v12004 AS
            SELECT
              m.*,
              COALESCE(c.certified_location,g.canonical_location) AS governed_location,
              c.decision AS certification_status,
              c.reviewer AS certified_by,
              c.decided_at AS certified_at,
              g.location_confidence,
              g.location_rule,
              g.quality_status AS reconciliation_status,
              g.quality_score,
              d.duplicate_group AS dedupe_group,
              d.duplicate_rank AS dedupe_rank,
              d.survivor_source_id AS dedupe_survivor_source_id
            FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            JOIN {CERT} c ON c.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {DUPS} d ON d.source_id=CAST(m.source_id AS TEXT)
            WHERE c.decision IN (\'AUTO_GOLD\',\'HUMAN_APPROVED\')
              AND COALESCE(d.duplicate_rank,1)=1
              AND g.conflict=FALSE
            """))

            c.execute(text(f"""
            CREATE VIEW pi_magazine_operational_v12004 AS
            SELECT
              m.*,
              COALESCE(c.certified_location,g.canonical_location) AS governed_location,
              c.decision AS certification_status,
              g.location_confidence,
              g.location_rule,
              g.quality_status AS reconciliation_status,
              g.quality_score,
              d.duplicate_group AS dedupe_group,
              d.duplicate_rank AS dedupe_rank,
              d.survivor_source_id AS dedupe_survivor_source_id
            FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {CERT} c ON c.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {DUPS} d ON d.source_id=CAST(m.source_id AS TEXT)
            WHERE COALESCE(c.decision,\'PENDING\') <> \'HUMAN_REJECTED\'
              AND g.quality_status IN (\'GOLD\',\'SILVER\')
              AND COALESCE(d.duplicate_rank,1)=1
            """))

            c.execute(text("""
            CREATE VIEW pi_magazine_ai_training_v12004 AS
            SELECT * FROM pi_magazine_certified_master_v12004
            """))

        with e.connect() as c:
            human_approved=int(c.execute(text(f"SELECT COUNT(*) FROM {CERT} WHERE decision='HUMAN_APPROVED'")).scalar() or 0)
            human_rejected=int(c.execute(text(f"SELECT COUNT(*) FROM {CERT} WHERE decision='HUMAN_REJECTED'")).scalar() or 0)
            certified=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_certified_master_v12004")).scalar() or 0)
            ai_count=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_ai_training_v12004")).scalar() or 0)
            pending_count=int(c.execute(text(f"SELECT COUNT(*) FROM {CERT} WHERE decision='PENDING'")).scalar() or 0)
            duplicate_rows=int(c.execute(text(f"SELECT COUNT(*) FROM {DUPS}")).scalar() or 0)

        STATE.update({
            "status":"PASS","phase":"COMPLETE","completed_at":_now(),"rows_processed":len(rows),
            "gold_seeded":gold_seeded,"pending_review":pending_count,"human_approved":human_approved,
            "human_rejected":human_rejected,"duplicate_groups":len(dup_groups),"duplicate_rows":duplicate_rows,
            "certified_unique":certified,"ai_training_rows":ai_count,
            "details":{
                "certified_view":"pi_magazine_certified_master_v12004",
                "operational_view":"pi_magazine_operational_v12004",
                "ai_training_view":"pi_magazine_ai_training_v12004",
                "dedupe_policy":"Strong fingerprint only; no source evidence deleted.",
                "human_review_policy":"SILVER/REVIEW must be approved or rejected by a person."
            }
        })
        with e.begin() as c:
            c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                      {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
    except Exception as exc:
        STATE["status"]="ERROR"; STATE["phase"]="FAILED"; STATE["completed_at"]=_now()
        STATE["error"]=f"{type(exc).__name__}: {exc}"
        STATE["details"]={"trace":traceback.format_exc()[-8000:]}
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                              {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
            except Exception:
                pass
    finally:
        if lock_conn is not None:
            try: lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception: pass
            try: lock_conn.close()
            except Exception: pass

def _start(core):
    threading.Thread(target=_build,args=(core,),daemon=True,name="certification-12004").start()

def _review_rows(e,status,q,page,per_page):
    off=(page-1)*per_page
    status=status.upper()
    where_status={
        "PENDING":"c.decision='PENDING'",
        "APPROVED":"c.decision='HUMAN_APPROVED'",
        "REJECTED":"c.decision='HUMAN_REJECTED'",
        "GOLD":"c.decision='AUTO_GOLD'",
        "SILVER":"g.quality_status='SILVER' AND c.decision='PENDING'",
        "REVIEW":"g.quality_status='REVIEW' AND c.decision='PENDING'",
        "QUARANTINED":"g.quality_status='QUARANTINED' AND c.decision='PENDING'",
    }.get(status,"c.decision='PENDING'")
    params={"q":q,"pat":"%"+q+"%","lim":per_page,"off":off}
    base=f"""
    FROM pi_magazine_master m
    JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
    JOIN {CERT} c ON c.source_id=CAST(m.source_id AS TEXT)
    LEFT JOIN {DUPS} d ON d.source_id=CAST(m.source_id AS TEXT)
    WHERE {where_status}
      AND (:q='' OR CAST(m.source_id AS TEXT) ILIKE :pat OR COALESCE(m.original_raw_text,'') ILIKE :pat
           OR COALESCE(g.canonical_location,'') ILIKE :pat)
    """
    with e.connect() as c:
        total=int(c.execute(text("SELECT COUNT(*) "+base),params).scalar() or 0)
        rs=[dict(r) for r in c.execute(text("""
        SELECT CAST(m.source_id AS TEXT) source_id,
               COALESCE(m.original_raw_text,'') original_raw_text,
               COALESCE(m.locality,'') raw_location,
               COALESCE(g.canonical_location,'') proposed_location,
               g.location_confidence,g.location_rule,g.quality_status,g.quality_score,g.conflict,
               c.decision,c.certified_location,c.reviewer,c.review_note,c.decided_at,
               d.duplicate_group,d.duplicate_rank,d.survivor_source_id,d.reason duplicate_reason
        """+base+"""
        ORDER BY
          CASE WHEN g.conflict THEN 0 ELSE 1 END,
          g.quality_score DESC,
          m.source_id
        LIMIT :lim OFFSET :off
        """),params).mappings().all()]
    return total,rs

def _workbench_html(e,status,q,page,per_page):
    total,rows=_review_rows(e,status,q,page,per_page)
    pages=max(1,(total+per_page-1)//per_page)
    nav=" ".join(f"<a href='/alliance/admin/magazine-certification?status={x}'>{x}</a>" for x in ("PENDING","SILVER","REVIEW","QUARANTINED","APPROVED","REJECTED","GOLD"))
    tr=[]
    for r in rows:
        sid=html.escape(str(r["source_id"]))
        proposed=html.escape(_norm(r["certified_location"] or r["proposed_location"]))
        ev=html.escape(f'{r["location_rule"]} · confidence {r["location_confidence"]} · {r["quality_status"]}')
        dup=""
        if r.get("duplicate_group"):
            dup=html.escape(f'{r["duplicate_group"]} rank {r["duplicate_rank"]} · survivor {r["survivor_source_id"]} · {r.get("duplicate_reason") or ""}')
        actions=""
        if r["decision"]=="PENDING":
            actions=f"""
            <form method='post' action='/alliance/admin/magazine-certification/{quote(str(r["source_id"]),safe="")}/approve'>
              <input name='location' value='{proposed}' required>
              <input name='note' placeholder='Verification note'>
              <button class='ok'>Approve</button>
            </form>
            <form method='post' action='/alliance/admin/magazine-certification/{quote(str(r["source_id"]),safe="")}/reject'>
              <input name='note' placeholder='Reason for rejection' required>
              <button class='bad'>Reject</button>
            </form>"""
        else:
            actions=html.escape(f'{r["decision"]} · {r.get("reviewer") or ""} · {r.get("review_note") or ""}')
        tr.append(f"""<tr>
        <td>{sid}</td><td>{html.escape(str(r["raw_location"]))}</td><td><b>{proposed}</b><br><small>{ev}</small></td>
        <td class='desc'>{html.escape(str(r["original_raw_text"]))}</td>
        <td>{dup}</td><td>{actions}</td></tr>""")
    prev=f"<a href='?status={quote(status)}&q={quote(q)}&page={page-1}'>← Previous</a>" if page>1 else ""
    nxt=f"<a href='?status={quote(status)}&q={quote(q)}&page={page+1}'>Next →</a>" if page<pages else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Magazine Certification</title><style>
    body{{font-family:Arial;background:#f5f7fa;color:#172033;margin:0}}main{{padding:16px}}
    nav a,.btn,button{{display:inline-block;padding:8px 10px;margin:2px;background:#17324d;color:#fff;text-decoration:none;border:0}}
    .ok{{background:#1f6f43}}.bad{{background:#9b1c1c}}table{{border-collapse:collapse;width:100%;background:#fff}}
    th,td{{border:1px solid #c9d0d8;padding:7px;vertical-align:top}}th{{background:#e9eef5;position:sticky;top:0}}
    td.desc{{min-width:330px;max-width:520px}}input{{padding:7px;margin:2px;width:100%;box-sizing:border-box}}
    .wrap{{overflow:auto;max-height:72vh}}small{{color:#667085}}
    </style></head><body><main>
    <h2>Alliance Magazine Certification & Duplicate Workbench · 12.0.4</h2>
    <p><b>Raw evidence is never deleted.</b> Approve only when the proposed/corrected locality is verified.</p>
    <nav>{nav} · <a href='/alliance/admin/data-governance-12003'>12.0.3 Reconciliation</a></nav>
    <form><input type='hidden' name='status' value='{html.escape(status)}'>
    <input name='q' value='{html.escape(q)}' placeholder='Search ID, description or locality' style='max-width:500px'><button>Search</button></form>
    <p>{total:,} records · Page {page}/{pages} {prev} {nxt}</p>
    <div class='wrap'><table><thead><tr>
    <th>ID</th><th>Raw Location</th><th>Proposed / Certified Location</th><th>Source Description</th><th>Duplicate</th><th>Decision</th>
    </tr></thead><tbody>{''.join(tr) if tr else '<tr><td colspan=6>No records</td></tr>'}</tbody></table></div>
    <p>{prev} {nxt}</p></main></body></html>"""

def _patch_magazine_screen():
    try:
        import alliance_cre_os_v1171 as cre
    except Exception:
        return False
    if getattr(cre,"_CERTIFIED_SCREEN_12004",False): return True
    original=cre.source_data
    def source_data(engine,k,q,page,per_page):
        if k!="magazine": return original(engine,k,q,page,per_page)
        off=(page-1)*per_page
        params={"q":q,"pat":"%"+q+"%","lim":per_page,"off":off}
        search="" if not q else " AND to_jsonb(m)::text ILIKE :pat "
        base=f"""
        FROM pi_magazine_master m
        JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
        LEFT JOIN {CERT} c ON c.source_id=CAST(m.source_id AS TEXT)
        LEFT JOIN {DUPS} d ON d.source_id=CAST(m.source_id AS TEXT)
        WHERE COALESCE(c.decision,'PENDING') <> 'HUMAN_REJECTED'
          AND g.quality_status IN ('GOLD','SILVER')
          AND COALESCE(d.duplicate_rank,1)=1
          {search}
          AND NOT EXISTS(
            SELECT 1 FROM ai_source_record_archives a
            WHERE a.source_type='magazine' AND a.source_record_id=CAST(m.source_id AS TEXT)
          )
        """
        with engine.connect() as c:
            total=int(c.execute(text("SELECT COUNT(*) "+base),params).scalar() or 0)
            rs=c.execute(text("""
            SELECT to_jsonb(m) ||
              jsonb_build_object(
                'locality',COALESCE(c.certified_location,g.canonical_location),
                'data_quality_status',
                  CASE WHEN c.decision='HUMAN_APPROVED' THEN 'GOLD_HUMAN'
                       WHEN c.decision='AUTO_GOLD' THEN 'GOLD'
                       ELSE g.quality_status END,
                'data_quality_score',g.quality_score,
                'location_quality_status',g.location_rule
              )
            """+base+""" ORDER BY m.source_id DESC NULLS LAST LIMIT :lim OFFSET :off
            """),params).scalars().all()
        return total,total,[r if isinstance(r,dict) else json.loads(r) for r in rs]
    cre.source_data=source_data
    cre._CERTIFIED_SCREEN_12004=True
    return True

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("12.0.4 requires app + engine")
    _setup(e)
    patched=_patch_magazine_screen()

    @app.get("/alliance/admin/magazine-certification",response_class=HTMLResponse)
    def workbench(req:Request,status:str="PENDING",q:str="",page:int=1,per_page:int=100):
        _login(core,req)
        return HTMLResponse(_workbench_html(e,status.upper(),q.strip(),max(1,page),max(25,min(per_page,200))),
                            headers={"Cache-Control":"no-store"})

    @app.post("/alliance/admin/magazine-certification/{source_id}/approve")
    async def approve(source_id:str,req:Request):
        reviewer=_login(core,req)
        f=await req.form()
        loc=_norm(f.get("location"))
        note=_norm(f.get("note"))
        if not _valid_location(loc):
            return HTMLResponse("<h3>Rejected: location must be geography only.</h3><p><a href='/alliance/admin/magazine-certification'>Back</a></p>",400)
        with e.begin() as c:
            exists=c.execute(text(f"SELECT 1 FROM {CERT} WHERE source_id=:sid"),{"sid":source_id}).scalar()
            if not exists: return HTMLResponse("Unknown source record",404)
            c.execute(text(f"""
            UPDATE {CERT} SET decision='HUMAN_APPROVED',certified_location=:loc,reviewer=:r,
              review_note=:n,decision_source='HUMAN',decided_at=NOW(),updated_at=NOW()
            WHERE source_id=:sid
            """),{"sid":source_id,"loc":loc,"r":str(reviewer),"n":note})
        _start(core)
        return RedirectResponse("/alliance/admin/magazine-certification?status=PENDING",303)

    @app.post("/alliance/admin/magazine-certification/{source_id}/reject")
    async def reject(source_id:str,req:Request):
        reviewer=_login(core,req)
        f=await req.form(); note=_norm(f.get("note"))
        if not note: return HTMLResponse("Rejection reason required",400)
        with e.begin() as c:
            c.execute(text(f"""
            UPDATE {CERT} SET decision='HUMAN_REJECTED',reviewer=:r,review_note=:n,
              decision_source='HUMAN',decided_at=NOW(),updated_at=NOW()
            WHERE source_id=:sid
            """),{"sid":source_id,"r":str(reviewer),"n":note})
        _start(core)
        return RedirectResponse("/alliance/admin/magazine-certification?status=PENDING",303)

    @app.get("/api/alliance/admin/magazine-certification/status")
    def status(req:Request):
        _login(core,req); return JSONResponse(dict(STATE))

    @app.post("/api/alliance/admin/magazine-certification/rebuild")
    def rebuild(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING": return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core); return {"status":"STARTED","version":VERSION}

    @app.get("/alliance/admin/magazine-certification-summary",response_class=HTMLResponse)
    def summary(req:Request):
        _login(core,req)
        s=dict(STATE)
        return HTMLResponse(f"""<!doctype html><html><body style='font-family:Arial;padding:25px'>
        <h2>Magazine Certification 12.0.4</h2>
        <pre>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <p><a href='/alliance/admin/magazine-certification'>Open Certification Workbench</a></p>
        </body></html>""")

    _start(core)
    return {
        "status":"REGISTERED","version":VERSION,"workbench":"/alliance/admin/magazine-certification",
        "summary":"/alliance/admin/magazine-certification-summary",
        "certified_view":"pi_magazine_certified_master_v12004",
        "operational_view":"pi_magazine_operational_v12004",
        "ai_training_view":"pi_magazine_ai_training_v12004",
        "magazine_screen_patched":patched
    }
