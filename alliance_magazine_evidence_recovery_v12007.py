
from __future__ import annotations

import html, json, re, threading, time, traceback
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.0.7.1-CONFLICT-GEOGRAPHY-FILTER"
STAGE = "pi_magazine_golden_stage_v12003"
CERT = "pi_magazine_certification_v12004"
DUPMAP = "pi_magazine_duplicate_map_v12005"
DUPDEC = "pi_magazine_duplicate_decisions_v12005"
GATE = "pi_magazine_evidence_promotion_gate_v120062"

RECOVERY = "pi_magazine_evidence_recovery_v12007"
CONFLICT_DEC = "pi_magazine_conflict_decisions_v12007"
AUDIT = "pi_magazine_evidence_recovery_audit_v12007"
RUNS = "pi_magazine_evidence_recovery_runs_v12007"
LOCK_KEY = 120070001

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
ADDRESS_RE = re.compile(r"^\s*(\d+[A-Z]?(?:/[0-9A-Z/-]+)+|[A-Z]{1,4}[-/]\d+[A-Z]?(?:/[0-9A-Z/-]+)*|[A-Z]{0,4}\d+[A-Z]?|\d+[A-Z]?)\b", re.I)

STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_processed":0,
    "silver_scanned":0,"needs_evidence_scanned":0,
    "recovered_candidates":0,"recovered_conflicts":0,
    "still_unproven":0,"existing_conflicts":0,"conflicts_resolved":0,
    "duplicate_blocked":0,"error":None,"details":{}
}
LOCK = threading.Lock()

def _now(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core, "app", None) or core
def _engine(core): return getattr(core, "engine", None)
def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+", " ", str(v or "")).strip()
def _key(v):
    s = PHONE_RE.sub(" ", _norm(v).upper())
    s = re.sub(r"[^A-Z0-9/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
def _addr(v):
    m = ADDRESS_RE.search(_norm(v))
    return _key(m.group(1)) if m else ""
def _area(v):
    m = AREA_RE.search(_norm(v))
    if not m: return ""
    unit = re.sub(r"[^A-Z]", "", m.group(2).upper())
    aliases = {"FT":"SQFT","SQFT":"SQFT","Y":"SQYD","YD":"SQYD","SQYD":"SQYD","SQM":"SQM","ACRE":"ACRE"}
    return f"{m.group(1)}:{aliases.get(unit,unit)}"
def _floor(v):
    m = FLOOR_RE.search(_norm(v))
    if not m: return ""
    u = _key(m.group(1))
    return {"BASEMENT":"BMT","GROUND FLOOR":"GF","FIRST FLOOR":"FF","SECOND FLOOR":"SF","THIRD FLOOR":"TF"}.get(u,u)
def _sig(v):
    a, ar, fl = _addr(v), _area(v), _floor(v)
    return "|".join((a,ar,fl)) if a and ar and fl else ""
SECTION_WORD_RE = re.compile(
    r"(?i)\b(?:RESIDENTIAL|COMMERCIAL|INDUSTRIAL|INSTITUTIONAL|HOSPITALITY|RETAIL|"
    r"SALE|RENT|LEASE|PURCHASE|BUY|SELL|REQUIREMENT|REQUIREMENTS|PROPERTY|PROPERTIES)\b"
)
MONTH_YEAR_RE = re.compile(
    r"(?i)\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)"
    r"(?:EMBER|OBER|UARY|RUARY|CH|IL|E|Y|UST)?[-\s/]*20\d{2}\b"
)

def _valid_loc(v):
    s = _norm(v)
    if not s or s.upper() in BAD:
        return False
    if SECTION_WORD_RE.search(s):
        return False
    if MONTH_YEAR_RE.search(s):
        return False
    if re.search(r"\b20\d{2}\b", s):
        return False
    if PHONE_RE.search(s) or AREA_RE.search(s):
        return False
    if len(s) > 75:
        return False
    return True

def _setup(e):
    with e.begin() as c:
        for t in (STAGE, CERT, DUPMAP, DUPDEC, GATE):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t":t}).scalar():
                raise RuntimeError(f"Required dependency missing: {t}")

        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RECOVERY}(
              source_id TEXT PRIMARY KEY,
              recovery_status TEXT NOT NULL,
              current_location TEXT,
              recovered_location TEXT,
              recovery_confidence INTEGER NOT NULL DEFAULT 0,
              recovery_rule TEXT,
              evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
              duplicate_group TEXT,
              duplicate_decision TEXT,
              source_conflict BOOLEAN NOT NULL DEFAULT FALSE,
              version TEXT NOT NULL,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {CONFLICT_DEC}(
              source_id TEXT PRIMARY KEY,
              decision TEXT NOT NULL DEFAULT 'PENDING',
              chosen_location TEXT,
              reviewer TEXT,
              note TEXT,
              decided_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ DEFAULT NOW(),
              CHECK(decision IN ('PENDING','RESOLVED','KEEP_UNRESOLVED'))
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {AUDIT}(
              id BIGSERIAL PRIMARY KEY,
              event_type TEXT NOT NULL,
              source_id TEXT,
              old_value TEXT,
              new_value TEXT,
              reviewer TEXT,
              note TEXT,
              created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RUNS}(
              id BIGSERIAL PRIMARY KEY,
              version TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TIMESTAMPTZ DEFAULT NOW(),
              completed_at TIMESTAMPTZ,
              summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
            )
        """))

def _wait_ready(e, timeout_seconds=120):
    deadline=time.monotonic()+timeout_seconds
    last={}
    while time.monotonic()<deadline:
        try:
            with e.connect() as c:
                raw=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_master")).scalar() or 0)
                stage=int(c.execute(text(f"""SELECT COUNT(*) FROM {STAGE}
                    WHERE version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'""")).scalar() or 0)
                row=c.execute(text("""SELECT status,summary FROM pi_magazine_governance_runs_v12003
                    ORDER BY id DESC LIMIT 1""")).mappings().first()
            status=row.get("status") if row else None
            summary=(row.get("summary") or {}) if row else {}
            if isinstance(summary,str):
                try: summary=json.loads(summary)
                except Exception: summary={}
            last={"raw_count":raw,"stage_count":stage,"status":status,
                  "phase":summary.get("phase"),"rows_total":summary.get("rows_total"),
                  "rows_scanned":summary.get("rows_scanned")}
            if raw>0 and stage==raw and status=="PASS" and summary.get("phase")=="COMPLETE" \
               and int(summary.get("rows_total") or 0)==raw and int(summary.get("rows_scanned") or 0)==raw:
                return last
        except Exception as exc:
            last={"error":f"{type(exc).__name__}: {exc}"}
        time.sleep(2)
    raise RuntimeError("12.0.3 final stage not ready: "+json.dumps(last,default=str))

def _table_exists(e,t):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())

def _load_layout(e):
    exact=defaultdict(list); structural=defaultdict(list)
    if not _table_exists(e,"pi_magazine_layout_evidence_v11921"):
        return exact,structural
    with e.connect() as c:
        rows=c.execute(text("""SELECT original_text,locality,page_number
            FROM pi_magazine_layout_evidence_v11921
            WHERE locality IS NOT NULL AND BTRIM(locality)<>''""")).mappings().all()
    for rr in rows:
        r=dict(rr); loc=_norm(r.get("locality"))
        if not _valid_loc(loc): continue
        desc=_norm(r.get("original_text"))
        exact[_key(desc)].append({"location":loc,"page":r.get("page_number"),"source":"LAYOUT"})
        s=_sig(desc)
        if s: structural[s].append({"location":loc,"page":r.get("page_number"),"source":"LAYOUT"})
    return exact,structural

def _load_complete(e):
    exact=defaultdict(list); structural=defaultdict(list)
    if not _table_exists(e,"pi_magazine_complete_v860"):
        return exact,structural
    with e.connect() as c:
        rows=c.execute(text("""SELECT source_record_id,original_description,description,
            original_section,location,page_number FROM pi_magazine_complete_v860""")).mappings().all()
    for rr in rows:
        r=dict(rr)
        desc=_norm(r.get("original_description") or r.get("description"))
        if not desc: continue
        locs=[]
        for candidate in (r.get("original_section"),r.get("location")):
            v=_norm(candidate)
            if _valid_loc(v) and v not in locs: locs.append(v)
        if not locs: continue
        for loc in locs:
            ev={"location":loc,"page":r.get("page_number"),"source":"COMPLETE",
                "source_record_id":r.get("source_record_id")}
            exact[_key(desc)].append(ev)
            s=_sig(desc)
            if s: structural[s].append(ev)
    return exact,structural

def _unique_locations(items):
    out=[]
    for x in items:
        v=_norm(x.get("location"))
        if _valid_loc(v) and v.casefold() not in [z.casefold() for z in out]:
            out.append(v)
    return out

def _recover(desc,current,layout_exact,layout_sig,complete_exact,complete_sig):
    k=_key(desc); s=_sig(desc)
    le=layout_exact.get(k,[]); ce=complete_exact.get(k,[])
    ls=layout_sig.get(s,[]) if s else []; cs=complete_sig.get(s,[]) if s else []

    exact_items=le+ce
    exact_locs=_unique_locations(exact_items)
    if len(exact_locs)==1:
        independent={x["source"] for x in exact_items if _norm(x.get("location")).casefold()==exact_locs[0].casefold()}
        conf=99 if len(independent)>=2 else 96
        return "RECOVERED_CANDIDATE",exact_locs[0],conf,"EXACT_SOURCE_RECOVERY",{
            "exact":exact_items,"structural":[],"independent_sources":sorted(independent)
        }
    if len(exact_locs)>1:
        return "RECOVERED_CONFLICT",None,0,"EXACT_SOURCE_CONFLICT",{"exact":exact_items,"locations":exact_locs}

    structural_items=ls+cs
    structural_locs=_unique_locations(structural_items)
    if len(structural_locs)==1 and _valid_loc(current) and structural_locs[0].casefold()==_norm(current).casefold():
        independent={x["source"] for x in structural_items if _norm(x.get("location")).casefold()==structural_locs[0].casefold()}
        if len(independent)>=2:
            return "RECOVERED_CANDIDATE",structural_locs[0],95,"STRUCTURAL_TWO_SOURCE_CONFIRMATION",{
                "exact":[],"structural":structural_items,"independent_sources":sorted(independent)
            }
        return "RECOVERED_CANDIDATE",structural_locs[0],93,"STRUCTURAL_SOURCE_PLUS_EXISTING_GEOGRAPHY",{
            "exact":[],"structural":structural_items,"independent_sources":sorted(independent)
        }
    if len(structural_locs)>1:
        return "RECOVERED_CONFLICT",None,0,"STRUCTURAL_SOURCE_CONFLICT",{
            "structural":structural_items,"locations":structural_locs
        }

    return "STILL_UNPROVEN",None,0,"NO_NEW_SOURCE_EVIDENCE",{"exact":[],"structural":[]}

def _build(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"SETUP","started_at":_now(),"completed_at":None,
            "rows_total":0,"rows_processed":0,"silver_scanned":0,"needs_evidence_scanned":0,
            "recovered_candidates":0,"recovered_conflicts":0,"still_unproven":0,
            "existing_conflicts":0,"conflicts_resolved":0,"duplicate_blocked":0,
            "error":None,"details":{}})

    lock_conn=None; run_id=None
    try:
        _setup(e)
        lock_conn=e.connect()
        if not bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar()):
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_RUN_ACTIVE","completed_at":_now()}); return

        STATE["phase"]="WAITING_FOR_FINAL_STAGE"
        readiness=_wait_ready(e)

        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),
                             {"v":VERSION}).scalar()

        STATE["phase"]="LOADING_SOURCE_EVIDENCE"
        lex,lsig=_load_layout(e)
        cex,csig=_load_complete(e)

        with e.connect() as c:
            rows=[dict(r) for r in c.execute(text(f"""
                SELECT g.source_id,g.canonical_location,g.location_confidence,g.location_rule,
                       g.quality_status,g.property_status,g.conflict,g.evidence,
                       m.original_raw_text,
                       d.duplicate_group,COALESCE(dd.decision,'PENDING') duplicate_decision,
                       COALESCE(cd.decision,'PENDING') conflict_decision
                FROM {STAGE} g
                JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=g.source_id
                LEFT JOIN {DUPMAP} d ON d.source_id=g.source_id
                LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=d.duplicate_group
                LEFT JOIN {CONFLICT_DEC} cd ON cd.source_id=g.source_id
                WHERE g.version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
                ORDER BY g.source_id
            """)).mappings().all()]

        STATE["rows_total"]=len(rows)
        counts=defaultdict(int)
        STATE["phase"]="RECOVERING_EVIDENCE"

        with e.begin() as c:
            for r in rows:
                q=_norm(r.get("quality_status")).upper()
                if q not in {"SILVER","REVIEW","QUARANTINED"} and not bool(r.get("conflict")):
                    continue
                if q=="SILVER": counts["silver_scanned"]+=1
                else: counts["needs_evidence_scanned"]+=1
                if bool(r.get("conflict")): counts["existing_conflicts"]+=1
                if _norm(r.get("conflict_decision")).upper()=="RESOLVED": counts["conflicts_resolved"]+=1

                status,loc,conf,rule,evidence=_recover(
                    r.get("original_raw_text"),r.get("canonical_location"),lex,lsig,cex,csig
                )

                if r.get("duplicate_group") and _norm(r.get("duplicate_decision")).upper()=="PENDING" \
                   and status=="RECOVERED_CANDIDATE":
                    status="RECOVERED_BLOCKED_DUPLICATE"
                    counts["duplicate_blocked"]+=1

                if status=="RECOVERED_CANDIDATE": counts["recovered_candidates"]+=1
                elif status=="RECOVERED_CONFLICT": counts["recovered_conflicts"]+=1
                elif status=="STILL_UNPROVEN": counts["still_unproven"]+=1

                c.execute(text(f"""
                    INSERT INTO {RECOVERY}(
                      source_id,recovery_status,current_location,recovered_location,
                      recovery_confidence,recovery_rule,evidence,duplicate_group,
                      duplicate_decision,source_conflict,version,updated_at
                    )
                    VALUES(:sid,:st,:cur,:loc,:conf,:rule,CAST(:ev AS JSONB),:dg,:dd,:sc,:v,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET
                      recovery_status=EXCLUDED.recovery_status,
                      current_location=EXCLUDED.current_location,
                      recovered_location=EXCLUDED.recovered_location,
                      recovery_confidence=EXCLUDED.recovery_confidence,
                      recovery_rule=EXCLUDED.recovery_rule,
                      evidence=EXCLUDED.evidence,
                      duplicate_group=EXCLUDED.duplicate_group,
                      duplicate_decision=EXCLUDED.duplicate_decision,
                      source_conflict=EXCLUDED.source_conflict,
                      version=EXCLUDED.version,
                      updated_at=NOW()
                """),{"sid":r["source_id"],"st":status,"cur":r.get("canonical_location"),
                       "loc":loc,"conf":conf,"rule":rule,"ev":json.dumps(evidence,ensure_ascii=False),
                       "dg":r.get("duplicate_group"),"dd":r.get("duplicate_decision"),
                       "sc":bool(r.get("conflict")),"v":VERSION})

        processed=counts["silver_scanned"]+counts["needs_evidence_scanned"]
        STATE.update({"status":"PASS","phase":"COMPLETE","completed_at":_now(),
            "rows_processed":processed,
            "silver_scanned":counts["silver_scanned"],
            "needs_evidence_scanned":counts["needs_evidence_scanned"],
            "recovered_candidates":counts["recovered_candidates"],
            "recovered_conflicts":counts["recovered_conflicts"],
            "still_unproven":counts["still_unproven"],
            "existing_conflicts":counts["existing_conflicts"],
            "conflicts_resolved":counts["conflicts_resolved"],
            "duplicate_blocked":counts["duplicate_blocked"],
            "details":{
                "raw_master_mutation":"NONE","stage_mutation":"NONE","certification_mutation":"NONE",
                "duplicate_mutation":"NONE","recovery_policy":"CANDIDATE_ONLY",
                "human_conflict_decisions":"SEPARATE_OVERLAY_ONLY",
                "non_geographic_section_headings_rejected":True,
                "conflict_candidates_geography_only":True,
                "exact_recovery":"unique locality from retained layout/complete evidence",
                "structural_recovery":"address+area+floor; must agree with existing geography; confidence 93/95",
                "final_stage_readiness":readiness,
                "recovery_table":RECOVERY,"conflict_decision_table":CONFLICT_DEC
            }})
        if run_id:
            with e.begin() as c:
                c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                          {"id":run_id,"s":json.dumps(STATE,default=str)})
    except Exception as exc:
        STATE.update({"status":"ERROR","phase":"FAILED","completed_at":_now(),
                      "error":f"{type(exc).__name__}: {exc}",
                      "details":{"trace":traceback.format_exc()[-7000:],
                                 "raw_master_mutation":"NONE","certification_mutation":"NONE"}})
    finally:
        if lock_conn is not None:
            try: lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception: pass
            try: lock_conn.close()
            except Exception: pass

def _start(core):
    threading.Thread(target=_build,args=(core,),daemon=True,name="mag-evidence-recovery-12007").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("12.0.7 requires app + engine")
    _setup(e)

    @app.get("/api/alliance/admin/magazine-evidence-recovery/status")
    def recovery_status():
        return JSONResponse(STATE)

    @app.post("/api/alliance/admin/magazine-evidence-recovery/rebuild")
    def recovery_rebuild():
        _start(core)
        return JSONResponse({"status":"STARTED","version":VERSION})

    @app.post("/alliance/admin/magazine-conflicts/{source_id}/resolve")
    def resolve_conflict(source_id:str, request:Request, chosen_location:str=Form(...), note:str=Form("")):
        reviewer=_login(core,request)
        with e.begin() as c:
            row=c.execute(text(f"""SELECT evidence FROM {STAGE} WHERE source_id=:sid"""),
                          {"sid":source_id}).mappings().first()
            if not row: return JSONResponse({"error":"source_id not found"},status_code=404)
            evidence=row.get("evidence") or {}
            if isinstance(evidence,str):
                try:evidence=json.loads(evidence)
                except Exception:evidence={}
            candidates=[]
            for x in evidence.get("candidates",[]) if isinstance(evidence,dict) else []:
                v=_norm(x.get("location"))
                if _valid_loc(v) and v.casefold() not in [z.casefold() for z in candidates]:
                    candidates.append(v)
            if _norm(chosen_location).casefold() not in [x.casefold() for x in candidates]:
                return JSONResponse({"error":"Chosen locality must be one of the retained evidence candidates.",
                                     "candidates":candidates},status_code=400)

            old=c.execute(text(f"SELECT decision,chosen_location FROM {CONFLICT_DEC} WHERE source_id=:sid"),
                          {"sid":source_id}).mappings().first()
            c.execute(text(f"""
                INSERT INTO {CONFLICT_DEC}(source_id,decision,chosen_location,reviewer,note,decided_at,updated_at)
                VALUES(:sid,'RESOLVED',:loc,:reviewer,:note,NOW(),NOW())
                ON CONFLICT(source_id) DO UPDATE SET decision='RESOLVED',
                  chosen_location=EXCLUDED.chosen_location,reviewer=EXCLUDED.reviewer,
                  note=EXCLUDED.note,decided_at=NOW(),updated_at=NOW()
            """),{"sid":source_id,"loc":chosen_location,"reviewer":str(reviewer),"note":note})
            c.execute(text(f"""INSERT INTO {AUDIT}(event_type,source_id,old_value,new_value,reviewer,note)
                VALUES('CONFLICT_RESOLVED',:sid,:old,:new,:reviewer,:note)"""),
                {"sid":source_id,"old":json.dumps(dict(old) if old else {}),
                 "new":chosen_location,"reviewer":str(reviewer),"note":note})
        return RedirectResponse("/alliance/admin/magazine-conflicts",status_code=303)

    @app.post("/alliance/admin/magazine-conflicts/{source_id}/keep-unresolved")
    def keep_unresolved(source_id:str, request:Request, note:str=Form("")):
        reviewer=_login(core,request)
        with e.begin() as c:
            c.execute(text(f"""
                INSERT INTO {CONFLICT_DEC}(source_id,decision,reviewer,note,decided_at,updated_at)
                VALUES(:sid,'KEEP_UNRESOLVED',:reviewer,:note,NOW(),NOW())
                ON CONFLICT(source_id) DO UPDATE SET decision='KEEP_UNRESOLVED',
                  chosen_location=NULL,reviewer=EXCLUDED.reviewer,note=EXCLUDED.note,
                  decided_at=NOW(),updated_at=NOW()
            """),{"sid":source_id,"reviewer":str(reviewer),"note":note})
        return RedirectResponse("/alliance/admin/magazine-conflicts",status_code=303)

    @app.get("/alliance/admin/magazine-conflicts",response_class=HTMLResponse)
    def conflict_page(req:Request,page:int=1):
        _login(core,req)
        page=max(1,int(page or 1)); per=30; off=(page-1)*per
        with e.connect() as c:
            rows=c.execute(text(f"""
                SELECT g.source_id,g.canonical_location,g.location_confidence,g.location_rule,
                       g.evidence,m.original_raw_text,
                       COALESCE(cd.decision,'PENDING') decision,cd.chosen_location,cd.note
                FROM {STAGE} g
                JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=g.source_id
                LEFT JOIN {CONFLICT_DEC} cd ON cd.source_id=g.source_id
                WHERE g.conflict=TRUE
                ORDER BY CASE WHEN COALESCE(cd.decision,'PENDING')='PENDING' THEN 0 ELSE 1 END,
                         g.source_id
                LIMIT :lim OFFSET :off
            """),{"lim":per,"off":off}).mappings().all()
        cards=[]
        for r in rows:
            ev=r["evidence"] or {}
            if isinstance(ev,str):
                try:ev=json.loads(ev)
                except Exception:ev={}
            candidates=[]
            for x in ev.get("candidates",[]) if isinstance(ev,dict) else []:
                loc=_norm(x.get("location"))
                if _valid_loc(loc) and loc.casefold() not in [z.casefold() for z in candidates]:
                    candidates.append(loc)
            opts="".join(f"<option value='{html.escape(x)}'>{html.escape(x)}</option>" for x in candidates)
            cards.append(f"""<div class='card'><b>{html.escape(str(r['source_id']))}</b> · decision {html.escape(str(r['decision']))}
              <br><b>Current:</b> {html.escape(str(r['canonical_location'] or 'MISSING'))} · {r['location_confidence'] or 0} · {html.escape(str(r['location_rule'] or ''))}
              <br><b>Description:</b> {html.escape(str(r['original_raw_text'] or ''))}
              <br><b>Evidence candidates:</b> {html.escape(', '.join(candidates) or 'none')}
              <form method='post' action='/alliance/admin/magazine-conflicts/{html.escape(str(r["source_id"]))}/resolve'>
                <select name='chosen_location' required>{opts}</select>
                <input name='note' placeholder='optional note'>
                <button type='submit'>Resolve</button>
              </form>
              <form method='post' action='/alliance/admin/magazine-conflicts/{html.escape(str(r["source_id"]))}/keep-unresolved'>
                <input name='note' placeholder='why unresolved'>
                <button type='submit'>Keep Unresolved</button>
              </form></div>""")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>Magazine Conflicts 12.0.7</title>
        <style>body{{font-family:Arial;margin:24px;background:#f5f2eb}}.card,.top{{background:white;padding:14px;border:1px solid #ddd;border-radius:10px;margin:10px 0;line-height:1.5}}form{{margin-top:8px}}</style>
        </head><body><h1>Magazine Conflict Workbench · 12.0.7</h1>
        <div class='top'>Human decisions are stored in a separate overlay. Raw master, 12.0.3 stage and certification are not changed.
        <br><a href='/alliance/admin/magazine-evidence-recovery'>Evidence Recovery</a> ·
        <a href='/alliance/admin/magazine-duplicate-review'>Duplicate Review</a> ·
        <a href='/api/alliance/admin/magazine-evidence-recovery/status'>Status JSON</a></div>
        {''.join(cards) if cards else "<div class='card'>No conflicts.</div>"}
        <p><a href='?page={page+1}'>Next page →</a></p></body></html>""")

    @app.get("/alliance/admin/magazine-evidence-recovery",response_class=HTMLResponse)
    def recovery_page(req:Request,bucket:str="RECOVERED_CANDIDATE",page:int=1):
        _login(core,req)
        allowed={"RECOVERED_CANDIDATE","RECOVERED_CONFLICT","RECOVERED_BLOCKED_DUPLICATE","STILL_UNPROVEN"}
        bucket=str(bucket or "RECOVERED_CANDIDATE").upper()
        if bucket not in allowed: bucket="RECOVERED_CANDIDATE"
        page=max(1,int(page or 1)); per=40; off=(page-1)*per
        with e.connect() as c:
            counts={r[0]:int(r[1]) for r in c.execute(text(f"SELECT recovery_status,COUNT(*) FROM {RECOVERY} GROUP BY recovery_status")).all()}
            rows=c.execute(text(f"""
                SELECT r.*,m.original_raw_text FROM {RECOVERY} r
                JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=r.source_id
                WHERE r.recovery_status=:b
                ORDER BY r.recovery_confidence DESC,r.source_id
                LIMIT :lim OFFSET :off
            """),{"b":bucket,"lim":per,"off":off}).mappings().all()
        cards=[]
        for r in rows:
            cards.append(f"""<div class='card'><b>{html.escape(str(r['source_id']))}</b> · {html.escape(str(r['recovery_status']))}
            · confidence {r['recovery_confidence'] or 0}
            <br><b>Current:</b> {html.escape(str(r['current_location'] or 'MISSING'))}
            <br><b>Recovered:</b> {html.escape(str(r['recovered_location'] or '—'))}
            <br><b>Rule:</b> {html.escape(str(r['recovery_rule'] or ''))}
            <br><b>Description:</b> {html.escape(str(r['original_raw_text'] or ''))}
            <br><b>Duplicate:</b> {html.escape(str(r['duplicate_group'] or 'none'))} · {html.escape(str(r['duplicate_decision'] or ''))}</div>""")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>Evidence Recovery 12.0.7</title>
        <style>body{{font-family:Arial;margin:24px;background:#f5f2eb}}.card,.top{{background:white;padding:14px;border:1px solid #ddd;border-radius:10px;margin:10px 0;line-height:1.5}}a{{margin-right:12px}}</style>
        </head><body><h1>Magazine Evidence Recovery · 12.0.7</h1>
        <div class='top'><b>Candidate-only.</b> Nothing is auto-certified.
        <br>Recovered Candidates <b>{counts.get('RECOVERED_CANDIDATE',0)}</b> ·
        Conflicts <b>{counts.get('RECOVERED_CONFLICT',0)}</b> ·
        Duplicate Blocked <b>{counts.get('RECOVERED_BLOCKED_DUPLICATE',0)}</b> ·
        Still Unproven <b>{counts.get('STILL_UNPROVEN',0)}</b><br><br>
        <a href='?bucket=RECOVERED_CANDIDATE'>Recovered Candidates</a>
        <a href='?bucket=RECOVERED_CONFLICT'>Recovered Conflicts</a>
        <a href='?bucket=RECOVERED_BLOCKED_DUPLICATE'>Duplicate Blocked</a>
        <a href='?bucket=STILL_UNPROVEN'>Still Unproven</a>
        <a href='/alliance/admin/magazine-conflicts'>45-Record Conflict Workbench</a>
        <a href='/api/alliance/admin/magazine-evidence-recovery/status'>Status JSON</a></div>
        {''.join(cards) if cards else "<div class='card'>No records in this bucket.</div>"}
        <p><a href='?bucket={bucket}&page={page+1}'>Next page →</a></p></body></html>""")

    _start(core)
    return {"status":"REGISTERED","version":VERSION,
            "status_api":"/api/alliance/admin/magazine-evidence-recovery/status",
            "recovery":"/alliance/admin/magazine-evidence-recovery",
            "conflicts":"/alliance/admin/magazine-conflicts",
            "policy":"CANDIDATE_ONLY_NO_AUTOCERTIFICATION"}
