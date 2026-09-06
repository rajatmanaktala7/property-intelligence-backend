
from __future__ import annotations

import hashlib, html, json, re, threading, traceback, time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.0.5.2-STARTUP-DEPENDENCY-GUARD"
STAGE = "pi_magazine_golden_stage_v12003"
CERT = "pi_magazine_certification_v12004"
DUPMAP = "pi_magazine_duplicate_map_v12005"
DUPDEC = "pi_magazine_duplicate_decisions_v12005"
AUDIT = "pi_magazine_certification_audit_v12005"
RUNS = "pi_magazine_hardening_runs_v12005"
LOCK_KEY = 120050001

PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
# Full leading property token. Compound slash addresses must remain intact:
# B-4/108 != B-4/28, K-1/113 != K-1/80, B-1/E-21 != B-1/A-6.
ADDRESS_RE = re.compile(
    r"^\s*(\d+[A-Z]?(?:/[0-9A-Z/-]+)+|[A-Z]{1,4}[-/]\d+[A-Z]?(?:/[0-9A-Z/-]+)*|[A-Z]{0,4}\d+[A-Z]?|\d+[A-Z]?)\b",
    re.I,
)
INVALID_GOVERNED_LOCATIONS = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}

STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_processed":0,"duplicate_groups":0,"duplicate_rows":0,
    "duplicate_pending":0,"duplicate_same_property":0,"duplicate_keep_separate":0,
    "certified_unique":0,"operational_rows":0,"ai_training_rows":0,
    "error":None,"details":{}
}
LOCK = threading.Lock()

def _now():
    return datetime.now(timezone.utc).isoformat()

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def _key(v):
    s = _norm(v).upper()
    s = PHONE_RE.sub(" ", s)
    s = re.sub(r"[^A-Z0-9/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _addr(desc):
    m = ADDRESS_RE.search(_norm(desc))
    return _key(m.group(1)) if m else ""

def _area(desc):
    m = AREA_RE.search(_norm(desc))
    if not m:
        return ""
    unit = re.sub(r"[^A-Z]", "", m.group(2).upper())
    aliases = {"FT":"SQFT","SQFT":"SQFT","Y":"SQYD","YD":"SQYD","SQYD":"SQYD","SQM":"SQM","ACRE":"ACRE"}
    return f"{m.group(1)}:{aliases.get(unit, unit)}"

def _floor(desc):
    m = FLOOR_RE.search(_norm(desc))
    if not m:
        return ""
    u = _key(m.group(1))
    return {"BASEMENT":"BMT","GROUND FLOOR":"GF","FIRST FLOOR":"FF","SECOND FLOOR":"SF","THIRD FLOOR":"TF"}.get(u, u)

def _phone(v):
    vals = PHONE_RE.findall(_norm(v))
    return vals[0] if vals else ""

def _transaction(row):
    for k in ("listing_type","transaction_type","category","property_type"):
        u = _norm(row.get(k)).upper()
        if "RENT" in u or "LEASE" in u:
            return "RENT"
        if "SALE" in u or "SELL" in u:
            return "SALE"
    return ""

def _master_columns(e):
    with e.connect() as c:
        cols = [r[0] for r in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='pi_magazine_master'
            ORDER BY ordinal_position
        """)).all()]
    low = {x.lower():x for x in cols}
    return {
        "source_id": low.get("source_id"),
        "desc": low.get("original_raw_text") or low.get("original_description") or low.get("description"),
        "phone": low.get("valid_mobiles") or low.get("contact_number") or low.get("contact_numbers"),
        "listing_type": low.get("listing_type"),
        "transaction_type": low.get("transaction_type"),
        "category": low.get("category"),
        "property_type": low.get("property_type"),
        "area": low.get("area"),
        "area_unit": low.get("area_unit"),
        "floor": low.get("floor"),
        "price": low.get("price"),
        "contact_name": low.get("contact_name_company") or low.get("contact_name"),
    }

def _select_rows(e):
    m = _master_columns(e)
    if not m["source_id"] or not m["desc"]:
        raise RuntimeError("pi_magazine_master source_id/description columns missing")
    optional = [k for k in ("phone","listing_type","transaction_type","category","property_type","area","area_unit","floor","price","contact_name") if m.get(k)]
    sel = [
        f'CAST(x."{m["source_id"]}" AS TEXT) AS source_id',
        f'COALESCE(x."{m["desc"]}", \'\') AS original_raw_text',
        "g.canonical_location","g.location_confidence","g.location_rule",
        "g.quality_status","g.quality_score","g.conflict"
    ]
    for k in optional:
        sel.append(f'COALESCE(CAST(x."{m[k]}" AS TEXT), \'\') AS "{k}"')
    sql = f"""
        SELECT {",".join(sel)}
        FROM pi_magazine_master x
        JOIN {STAGE} g ON g.source_id=CAST(x."{m["source_id"]}" AS TEXT)
        WHERE g.version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
        ORDER BY x."{m["source_id"]}"
    """
    with e.connect() as c:
        return [dict(r) for r in c.execute(text(sql)).mappings().all()]

def _dup_key(row):
    raw_loc = _norm(row.get("canonical_location"))
    loc = _key(raw_loc)
    desc = _norm(row.get("original_raw_text"))
    addr = _addr(desc)
    area = _area(desc) or _key(f'{row.get("area","")} {row.get("area_unit","")}')
    floor = _floor(desc) or _key(row.get("floor"))
    txn = _transaction(row)
    phone = _phone(row.get("phone",""))

    # PROPERTY5 requires a real governed locality.
    # MISSING/UNKNOWN must never receive 96% duplicate confidence.
    usable_loc = bool(raw_loc) and raw_loc.upper() not in INVALID_GOVERNED_LOCATIONS

    if usable_loc and loc and addr and area and floor and txn:
        return (
            "PROPERTY5", loc, addr, area, floor, txn
        ), 96, "Governed locality + exact full leading address token + area + floor + transaction"

    # Strict fallback: identical normalized ad plus the same phone.
    exact = _key(desc)
    if exact and phone:
        return ("TEXTPHONE",exact,phone), 94, "Exact normalized ad + same phone"

    return None, 0, ""

def _survivor_score(row):
    score = {"GOLD":1000,"SILVER":700,"REVIEW":300,"QUARANTINED":100}.get(row.get("quality_status"),0)
    score += int(row.get("quality_score") or 0) + int(row.get("location_confidence") or 0)
    if _phone(row.get("phone","")): score += 20
    if _norm(row.get("contact_name")): score += 10
    if _norm(row.get("price")): score += 5
    return score

def _setup(e):
    with e.begin() as c:
        for t in (STAGE, CERT):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t":t}).scalar():
                raise RuntimeError(f"Required table missing: {t}")
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {DUPMAP}(
              source_id TEXT PRIMARY KEY,
              duplicate_group TEXT NOT NULL,
              duplicate_rank INTEGER NOT NULL,
              duplicate_confidence INTEGER NOT NULL,
              fingerprint TEXT NOT NULL,
              reason TEXT NOT NULL,
              suggested_survivor_source_id TEXT NOT NULL,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {DUPDEC}(
              duplicate_group TEXT PRIMARY KEY,
              decision TEXT NOT NULL DEFAULT 'PENDING',
              reviewer TEXT, review_note TEXT, decided_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
              CHECK(decision IN ('PENDING','SAME_PROPERTY','KEEP_SEPARATE'))
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {AUDIT}(
              id BIGSERIAL PRIMARY KEY, event_type TEXT NOT NULL,
              source_id TEXT, duplicate_group TEXT, old_value TEXT, new_value TEXT,
              reviewer TEXT, note TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RUNS}(
              id BIGSERIAL PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL,
              started_at TIMESTAMPTZ DEFAULT NOW(), completed_at TIMESTAMPTZ,
              summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
            )
        """))

def _rebuild_views(e):
    with e.begin() as c:
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12005"))
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12005"))
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_certified_master_v12005"))
        c.execute(text(f"""
            CREATE VIEW pi_magazine_certified_master_v12005 AS
            SELECT m.*,
              COALESCE(c.certified_location,g.canonical_location) AS governed_location,
              c.decision AS certification_status,
              c.reviewer AS certified_by,c.decided_at AS certified_at,
              g.location_confidence,g.location_rule,
              g.quality_status AS reconciliation_status,g.quality_score,
              g.conflict AS source_conflict,
              d.duplicate_group AS dedupe_group,d.duplicate_rank AS dedupe_rank,
              d.duplicate_confidence AS dedupe_confidence,
              d.suggested_survivor_source_id AS dedupe_suggested_survivor,
              COALESCE(dd.decision,'PENDING') AS dedupe_decision
            FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            JOIN {CERT} c ON c.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {DUPMAP} d ON d.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=d.duplicate_group
            WHERE c.decision IN ('AUTO_GOLD','HUMAN_APPROVED')
              AND (g.conflict=FALSE OR c.decision='HUMAN_APPROVED')
              AND NOT (COALESCE(dd.decision,'PENDING')='SAME_PROPERTY' AND COALESCE(d.duplicate_rank,1)>1)
        """))
        c.execute(text(f"""
            CREATE VIEW pi_magazine_operational_v12005 AS
            SELECT m.*,
              COALESCE(c.certified_location,g.canonical_location) AS governed_location,
              c.decision AS certification_status,
              g.location_confidence,g.location_rule,
              g.quality_status AS reconciliation_status,g.quality_score,
              g.conflict AS source_conflict,
              d.duplicate_group AS dedupe_group,d.duplicate_rank AS dedupe_rank,
              d.duplicate_confidence AS dedupe_confidence,
              d.suggested_survivor_source_id AS dedupe_suggested_survivor,
              COALESCE(dd.decision,'PENDING') AS dedupe_decision
            FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {CERT} c ON c.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {DUPMAP} d ON d.source_id=CAST(m.source_id AS TEXT)
            LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=d.duplicate_group
            WHERE COALESCE(c.decision,'PENDING') <> 'HUMAN_REJECTED'
              AND (g.quality_status IN ('GOLD','SILVER') OR c.decision='HUMAN_APPROVED')
              AND NOT (COALESCE(dd.decision,'PENDING')='SAME_PROPERTY' AND COALESCE(d.duplicate_rank,1)>1)
        """))
        c.execute(text("""
            CREATE VIEW pi_magazine_ai_training_v12005 AS
            SELECT * FROM pi_magazine_certified_master_v12005
        """))

def _build(core):
    e = _engine(core)
    if e is None: return
    with LOCK:
        if STATE["status"] == "RUNNING": return
        STATE.update({"status":"RUNNING","phase":"SETUP","started_at":_now(),"completed_at":None,
                      "rows_total":0,"rows_processed":0,"duplicate_groups":0,"duplicate_rows":0,
                      "duplicate_pending":0,"duplicate_same_property":0,"duplicate_keep_separate":0,
                      "certified_unique":0,"operational_rows":0,"ai_training_rows":0,"error":None,"details":{}})
    lock_conn = None
    run_id = None
    try:
        _setup(e)
        lock_conn = e.connect()
        if not bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar()):
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_HARDENING_RUN_ACTIVE","completed_at":_now()})
            return
        with e.begin() as c:
            run_id = c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
        STATE["phase"] = "WAITING_FOR_GOVERNED_ROWS"
        rows = []
        wait_attempts = 0
        for wait_attempts in range(1, 31):
            rows = _select_rows(e)
            if rows:
                break
            STATE["details"] = {
                "startup_dependency": "WAITING_FOR_12.0.3_STAGE",
                "wait_attempt": wait_attempts,
                "max_attempts": 30,
            }
            time.sleep(2)

        if not rows:
            raise RuntimeError(
                "12.0.5.2 startup guard: governed stage returned 0 rows after 60 seconds. "
                "Refusing to publish PASS with an empty certification dataset."
            )

        STATE["phase"] = "LOADING_GOVERNED_ROWS"
        STATE["rows_total"] = len(rows)
        STATE["phase"] = "BUILDING_DUPLICATE_CANDIDATES"
        groups = defaultdict(list)
        for r in rows:
            key, conf, reason = _dup_key(r)
            if key:
                groups[key].append((r,conf,reason))
        candidates = [(k,v) for k,v in groups.items() if len(v)>1]
        with e.begin() as c:
            c.execute(text(f"DELETE FROM {DUPMAP}"))
            for finger, vals in candidates:
                fp = "|".join(map(str,finger))
                gid = "MAGD5-" + hashlib.sha1(fp.encode("utf-8")).hexdigest()[:12].upper()
                ranked = sorted(vals,key=lambda x:(_survivor_score(x[0]),x[0]["source_id"]),reverse=True)
                survivor = ranked[0][0]["source_id"]
                for rank,(r,conf,reason) in enumerate(ranked,1):
                    c.execute(text(f"""
                        INSERT INTO {DUPMAP}
                        (source_id,duplicate_group,duplicate_rank,duplicate_confidence,fingerprint,reason,suggested_survivor_source_id,updated_at)
                        VALUES(:sid,:g,:r,:c,:f,:why,:s,NOW())
                    """),{"sid":r["source_id"],"g":gid,"r":rank,"c":conf,"f":fp,"why":reason,"s":survivor})
                c.execute(text(f"""
                    INSERT INTO {DUPDEC}(duplicate_group,decision,updated_at)
                    VALUES(:g,'PENDING',NOW()) ON CONFLICT(duplicate_group) DO NOTHING
                """),{"g":gid})
        STATE["phase"] = "BUILDING_SAFE_VIEWS"
        _rebuild_views(e)
        with e.connect() as c:
            duplicate_rows = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPMAP}")).scalar() or 0)
            pending = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPDEC} WHERE decision='PENDING' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})")).scalar() or 0)
            same = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPDEC} WHERE decision='SAME_PROPERTY' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})")).scalar() or 0)
            separate = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPDEC} WHERE decision='KEEP_SEPARATE' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})")).scalar() or 0)
            certified = int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_certified_master_v12005")).scalar() or 0)
            operational = int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_operational_v12005")).scalar() or 0)
            airows = int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_ai_training_v12005")).scalar() or 0)
        STATE.update({"status":"PASS","phase":"COMPLETE","completed_at":_now(),"rows_processed":len(rows),
                      "duplicate_groups":len(candidates),"duplicate_rows":duplicate_rows,
                      "duplicate_pending":pending,"duplicate_same_property":same,"duplicate_keep_separate":separate,
                      "certified_unique":certified,"operational_rows":operational,"ai_training_rows":airows,
                      "details":{"raw_master_mutation":"NONE",
                                 "dedupe_policy":"CANDIDATE_ONLY_UNTIL_HUMAN_DECISION",
                                 "slash_address_fix":True,"full_compound_address_identity":True,"missing_location_property5_blocked":True,"startup_dependency_guard":True,"zero_row_pass_blocked":True,
                                 "human_approved_review_operational":True,
                                 "human_approved_conflict_resolution":True,
                                 "certified_view":"pi_magazine_certified_master_v12005",
                                 "operational_view":"pi_magazine_operational_v12005",
                                 "ai_training_view":"pi_magazine_ai_training_v12005"}})
        if run_id:
            with e.begin() as c:
                c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                          {"id":run_id,"s":json.dumps(STATE,default=str)})
    except Exception as exc:
        STATE.update({"status":"ERROR","phase":"FAILED","completed_at":_now(),
                      "error":f"{type(exc).__name__}: {exc}","details":{"trace":traceback.format_exc()}})
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                              {"id":run_id,"s":json.dumps(STATE,default=str)})
            except Exception:
                pass
    finally:
        if lock_conn is not None:
            try: lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception: pass
            try: lock_conn.close()
            except Exception: pass

def _start(core):
    threading.Thread(target=_build,args=(core,),daemon=True).start()

def register(core):
    app = _app(core)
    e = _engine(core)
    if e is None:
        return {"status":"ERROR","version":VERSION,"error":"engine missing"}
    _setup(e)

    @app.get("/api/alliance/admin/magazine-hardening/status")
    def status_api():
        return JSONResponse(STATE)

    @app.post("/api/alliance/admin/magazine-hardening/rebuild")
    def rebuild_api():
        _start(core)
        return JSONResponse({"status":"STARTED","version":VERSION})

    @app.get("/alliance/admin/magazine-hardening",response_class=HTMLResponse)
    def dashboard(req: Request):
        _login(core,req)
        with e.connect() as c:
            pending_cert = int(c.execute(text(f"SELECT COUNT(*) FROM {CERT} WHERE decision='PENDING'")).scalar() or 0)
            pending_dup = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPDEC} WHERE decision='PENDING' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})")).scalar() or 0)
            same = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPDEC} WHERE decision='SAME_PROPERTY' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})")).scalar() or 0)
            separate = int(c.execute(text(f"SELECT COUNT(*) FROM {DUPDEC} WHERE decision='KEEP_SEPARATE' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})")).scalar() or 0)
        return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><title>Magazine Hardening 12.0.5</title>
        <style>body{{font-family:Arial;margin:30px;background:#f5f2eb;color:#27231f}}.card{{background:white;padding:16px;margin:12px 0;border:1px solid #ddd;border-radius:12px}}a{{color:#274c77}}</style></head>
        <body><h1>Magazine Certification Hardening · 12.0.5</h1>
        <div class="card"><b>Safety:</b> duplicate candidates are not merged automatically. Raw magazine evidence is never deleted.</div>
        <div class="card">Certification pending: <b>{pending_cert}</b><br>Duplicate review pending: <b>{pending_dup}</b><br>Same Property confirmed: <b>{same}</b><br>Keep Separate: <b>{separate}</b></div>
        <div class="card"><a href="/alliance/admin/magazine-certification">Certification Workbench</a><br><br>
        <a href="/alliance/admin/magazine-duplicate-review">Duplicate Review Workbench</a><br><br>
        <a href="/api/alliance/admin/magazine-hardening/status">12.0.5 Status JSON</a></div></body></html>""")

    @app.get("/alliance/admin/magazine-duplicate-review",response_class=HTMLResponse)
    def duplicate_review(req: Request,status: str="PENDING",page: int=1):
        _login(core,req)
        status = status.upper()
        if status not in {"PENDING","SAME_PROPERTY","KEEP_SEPARATE","ALL"}: status="PENDING"
        page=max(1,int(page or 1)); per=20; off=(page-1)*per
        where="" if status=="ALL" else "WHERE COALESCE(dd.decision,'PENDING')=:status"
        params={"limit":per,"off":off}
        if status!="ALL": params["status"]=status
        with e.connect() as c:
            groups=c.execute(text(f"""
                SELECT d.duplicate_group,MAX(d.duplicate_confidence) confidence,MAX(d.reason) reason,
                       MAX(d.suggested_survivor_source_id) survivor,
                       COALESCE(MAX(dd.decision),'PENDING') decision,COUNT(*) row_count
                FROM {DUPMAP} d LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=d.duplicate_group
                {where}
                GROUP BY d.duplicate_group ORDER BY d.duplicate_group LIMIT :limit OFFSET :off
            """),params).mappings().all()
            cards=[]
            for g in groups:
                rows=c.execute(text(f"""
                    SELECT d.source_id,d.duplicate_rank,g.canonical_location,g.quality_status,m.original_raw_text
                    FROM {DUPMAP} d
                    JOIN {STAGE} g ON g.source_id=d.source_id
                    JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=d.source_id
                    WHERE d.duplicate_group=:gid ORDER BY d.duplicate_rank,d.source_id
                """),{"gid":g["duplicate_group"]}).mappings().all()
                trs="".join(f"<tr><td>{r['duplicate_rank']}</td><td>{html.escape(str(r['source_id']))}</td><td>{html.escape(str(r['canonical_location'] or 'MISSING'))}</td><td>{html.escape(str(r['quality_status'] or ''))}</td><td>{html.escape(str(r['original_raw_text'] or ''))}</td></tr>" for r in rows)
                gid=html.escape(g["duplicate_group"])
                cards.append(f"""<div class="card"><h3>{gid} · {g['row_count']} rows · confidence {g['confidence']}</h3>
                <p><b>Reason:</b> {html.escape(str(g['reason'] or ''))}</p><p><b>Suggested survivor:</b> {html.escape(str(g['survivor'] or ''))}</p>
                <table><tr><th>Rank</th><th>ID</th><th>Location</th><th>Quality</th><th>Description</th></tr>{trs}</table>
                <div class="actions"><form method="post" action="/alliance/admin/magazine-duplicate-review/{gid}/same"><input name="note" placeholder="note"><button>Same Property</button></form>
                <form method="post" action="/alliance/admin/magazine-duplicate-review/{gid}/separate"><input name="note" placeholder="note"><button>Keep Separate</button></form>
                <form method="post" action="/alliance/admin/magazine-duplicate-review/{gid}/reset"><button>Reset</button></form></div></div>""")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><title>Duplicate Review</title>
        <style>body{{font-family:Arial;margin:24px;background:#f5f2eb}}.card{{background:#fff;padding:15px;margin:12px 0;border:1px solid #ddd;border-radius:12px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border:1px solid #ddd;padding:6px;vertical-align:top}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}form{{display:flex;gap:5px}}button,input{{padding:7px}}</style></head>
        <body><h1>Duplicate Review Workbench · 12.0.5</h1><p><a href="/alliance/admin/magazine-hardening">← Hardening Dashboard</a></p>
        <p><a href="?status=PENDING">Pending</a> · <a href="?status=SAME_PROPERTY">Same Property</a> · <a href="?status=KEEP_SEPARATE">Keep Separate</a> · <a href="?status=ALL">All</a></p>
        {''.join(cards) if cards else '<div class="card">No groups in this filter.</div>'}
        <p><a href="?status={status}&page={page+1}">Next page →</a></p></body></html>""")

    def _decide(req,gid,decision,note=""):
        reviewer=str(_login(core,req) or "team")
        with e.begin() as c:
            exists=bool(c.execute(text(f"SELECT EXISTS(SELECT 1 FROM {DUPMAP} WHERE duplicate_group=:g)"),{"g":gid}).scalar())
            if not exists: return HTMLResponse("Duplicate group not found.",status_code=404)
            old=c.execute(text(f"SELECT decision FROM {DUPDEC} WHERE duplicate_group=:g"),{"g":gid}).scalar() or "PENDING"
            c.execute(text(f"""
                INSERT INTO {DUPDEC}(duplicate_group,decision,reviewer,review_note,decided_at,updated_at)
                VALUES(:g,:d,:r,:n,NOW(),NOW())
                ON CONFLICT(duplicate_group) DO UPDATE SET decision=EXCLUDED.decision,reviewer=EXCLUDED.reviewer,
                review_note=EXCLUDED.review_note,decided_at=NOW(),updated_at=NOW()
            """),{"g":gid,"d":decision,"r":reviewer,"n":note})
            c.execute(text(f"""
                INSERT INTO {AUDIT}(event_type,duplicate_group,old_value,new_value,reviewer,note)
                VALUES('DUPLICATE_DECISION',:g,:o,:nval,:r,:note)
            """),{"g":gid,"o":old,"nval":decision,"r":reviewer,"note":note})
        return RedirectResponse("/alliance/admin/magazine-duplicate-review?status=PENDING",status_code=303)

    @app.post("/alliance/admin/magazine-duplicate-review/{gid}/same")
    async def same(req:Request,gid:str):
        form=await req.form()
        return _decide(req,gid,"SAME_PROPERTY",_norm(form.get("note")))

    @app.post("/alliance/admin/magazine-duplicate-review/{gid}/separate")
    async def separate(req:Request,gid:str):
        form=await req.form()
        return _decide(req,gid,"KEEP_SEPARATE",_norm(form.get("note")))

    @app.post("/alliance/admin/magazine-duplicate-review/{gid}/reset")
    async def reset(req:Request,gid:str):
        return _decide(req,gid,"PENDING","Reset for re-review")

    _start(core)
    return {"status":"REGISTERED","version":VERSION,
            "dashboard":"/alliance/admin/magazine-hardening",
            "duplicate_review":"/alliance/admin/magazine-duplicate-review",
            "status_api":"/api/alliance/admin/magazine-hardening/status",
            "write_policy":"NO_RAW_MASTER_MUTATION",
            "dedupe_policy":"HUMAN_DECISION_REQUIRED"}
