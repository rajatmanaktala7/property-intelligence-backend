
from __future__ import annotations

import html, json, threading, traceback, time
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.6.3-EVIDENCE-PROMOTION-GATE-DEPENDENCY-GUARD"
STAGE = "pi_magazine_golden_stage_v12003"
CERT = "pi_magazine_certification_v12004"
DUPMAP = "pi_magazine_duplicate_map_v12005"
DUPDEC = "pi_magazine_duplicate_decisions_v12005"
GATE = "pi_magazine_evidence_promotion_gate_v120062"
RUNS = "pi_magazine_evidence_promotion_runs_v120062"
LOCK_KEY = 120062001

PROVEN_RULES = {
    "EXPLICIT_LOCATION_IN_DESCRIPTION",
    "EXACT_LAYOUT_MATCH",
    "EXACT_COMPLETE_MATCH",
}
BAD_LOCATIONS = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}

STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_scored":0,
    "proven_already_certified":0,
    "proven_blocked_duplicate":0,
    "proven_blocked_conflict":0,
    "proven_candidate":0,
    "silver_not_proven":0,
    "needs_evidence":0,
    "excluded_non_property":0,
    "error":None,
    "details":{}
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

def _setup(e):
    with e.begin() as c:
        for t in (STAGE, CERT, DUPMAP, DUPDEC):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}).scalar():
                raise RuntimeError(f"Required dependency missing: {t}")

        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {GATE}(
              source_id TEXT PRIMARY KEY,
              gate_status TEXT NOT NULL,
              evidence_rule TEXT,
              evidence_confidence INTEGER NOT NULL DEFAULT 0,
              canonical_location TEXT,
              quality_status TEXT,
              property_status TEXT,
              source_conflict BOOLEAN NOT NULL DEFAULT FALSE,
              certification_status TEXT,
              duplicate_group TEXT,
              duplicate_decision TEXT,
              reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
              blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
              version TEXT NOT NULL,
              updated_at TIMESTAMPTZ DEFAULT NOW()
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

def _classify(r):
    reasons = []
    blockers = []

    q = str(r.get("quality_status") or "")
    ps = str(r.get("property_status") or "")
    loc = str(r.get("canonical_location") or "")
    rule = str(r.get("location_rule") or "")
    conf = int(r.get("location_confidence") or 0)
    conflict = bool(r.get("conflict"))
    cert = str(r.get("cert_decision") or "PENDING")
    dg = r.get("duplicate_group")
    dd = str(r.get("duplicate_decision") or "PENDING")

    if q == "EXCLUDED_NON_PROPERTY" or ps == "NON_PROPERTY":
        return "EXCLUDED_NON_PROPERTY", reasons, ["Non-property classification"]

    valid_loc = loc.strip().upper() not in BAD_LOCATIONS
    proven = valid_loc and rule in PROVEN_RULES and conf >= 93

    if proven:
        reasons.append(f"Direct source evidence: {rule}")
        reasons.append(f"Location confidence {conf}")
        reasons.append(f"Canonical locality: {loc}")

        if cert in ("AUTO_GOLD", "HUMAN_APPROVED"):
            return "PROVEN_ALREADY_CERTIFIED", reasons, blockers

        if conflict:
            blockers.append("Competing source evidence must be resolved by a person")
            return "PROVEN_BLOCKED_CONFLICT", reasons, blockers

        if dg and dd == "PENDING":
            blockers.append("Duplicate group still awaits human decision")
            return "PROVEN_BLOCKED_DUPLICATE", reasons, blockers

        # This is the only class that could be considered for future bulk promotion.
        # 12.0.6.2 itself performs NO certification mutation.
        if ps not in ("NON_PROPERTY", "WEAK_PROPERTY_EVIDENCE"):
            return "PROVEN_CANDIDATE", reasons, blockers

        blockers.append("Property identity evidence is incomplete")
        return "NEEDS_EVIDENCE", reasons, blockers

    if q == "SILVER":
        reasons.append("Record is operational SILVER")
        blockers.append("Locality is not independently proven by direct/layout/complete evidence")
        if rule == "EXISTING_VALID_GEOGRAPHY":
            blockers.append("Existing geography alone is not enough for certification")
        return "SILVER_NOT_PROVEN", reasons, blockers

    if not valid_loc:
        blockers.append("No governed locality")
    elif rule not in PROVEN_RULES:
        blockers.append(f"Location rule is not promotion-grade: {rule or 'NONE'}")
    if conflict:
        blockers.append("Source conflict")
    if dg and dd == "PENDING":
        blockers.append("Pending duplicate decision")
    return "NEEDS_EVIDENCE", reasons, blockers


def _wait_for_final_stage(e, timeout_seconds=120):
    """
    Wait until the 12.0.3 governed stage is fully rebuilt and matches raw master.
    """
    deadline = time.monotonic() + timeout_seconds
    last = {}

    while time.monotonic() < deadline:
        try:
            with e.connect() as c:
                raw_count = int(c.execute(text(
                    "SELECT COUNT(*) FROM pi_magazine_master"
                )).scalar() or 0)

                stage_count = int(c.execute(text(f"""
                    SELECT COUNT(*)
                    FROM {STAGE}
                    WHERE version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
                """)).scalar() or 0)

                row = c.execute(text("""
                    SELECT status, summary
                    FROM pi_magazine_governance_runs_v12003
                    ORDER BY id DESC
                    LIMIT 1
                """)).mappings().first()

            status = None
            summary = {}
            if row:
                status = row.get("status")
                summary = row.get("summary") or {}
                if isinstance(summary, str):
                    try:
                        summary = json.loads(summary)
                    except Exception:
                        summary = {}

            phase = str(summary.get("phase") or "")
            rows_total = int(summary.get("rows_total") or 0)
            rows_scanned = int(summary.get("rows_scanned") or 0)

            last = {
                "raw_count": raw_count,
                "stage_count": stage_count,
                "governance_status": status,
                "governance_phase": phase,
                "governance_rows_total": rows_total,
                "governance_rows_scanned": rows_scanned,
            }

            if (
                raw_count > 0
                and stage_count == raw_count
                and status == "PASS"
                and phase == "COMPLETE"
                and rows_total == raw_count
                and rows_scanned == raw_count
            ):
                return last

        except Exception as exc:
            last = {"check_error": f"{type(exc).__name__}: {exc}"}

        time.sleep(2)

    raise RuntimeError(
        "12.0.3 final governed stage was not ready within 120 seconds. "
        + json.dumps(last, default=str)
    )

def _build(core):
    e = _engine(core)
    if e is None:
        return

    with LOCK:
        if STATE["status"] == "RUNNING":
            return
        STATE.update({
            "status":"RUNNING","phase":"SETUP","started_at":_now(),"completed_at":None,
            "rows_total":0,"rows_scored":0,
            "proven_already_certified":0,"proven_blocked_duplicate":0,
            "proven_blocked_conflict":0,"proven_candidate":0,
            "silver_not_proven":0,"needs_evidence":0,
            "excluded_non_property":0,"error":None,"details":{}
        })

    lock_conn = None
    run_id = None

    try:
        _setup(e)
        lock_conn = e.connect()
        if not bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()):
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_RUN_ACTIVE","completed_at":_now()})
            return

        STATE["phase"] = "WAITING_FOR_12.0.3_FINAL_STAGE"
        readiness = _wait_for_final_stage(e, timeout_seconds=120)

        with e.begin() as c:
            run_id = c.execute(text(f"""
                INSERT INTO {RUNS}(version,status)
                VALUES(:v,'RUNNING')
                RETURNING id
            """), {"v": VERSION}).scalar()

        STATE["phase"] = "READING_GOVERNED_EVIDENCE"

        with e.connect() as c:
            rows = [dict(x) for x in c.execute(text(f"""
                SELECT
                  g.source_id,
                  g.canonical_location,
                  g.location_confidence,
                  g.location_rule,
                  g.quality_status,
                  g.property_status,
                  g.conflict,
                  g.evidence,
                  COALESCE(c.decision,'PENDING') AS cert_decision,
                  d.duplicate_group,
                  COALESCE(dd.decision,'PENDING') AS duplicate_decision
                FROM {STAGE} g
                LEFT JOIN {CERT} c ON c.source_id=g.source_id
                LEFT JOIN {DUPMAP} d ON d.source_id=g.source_id
                LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=d.duplicate_group
                WHERE g.version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
                ORDER BY g.source_id
            """)).mappings().all()]

        if not rows:
            raise RuntimeError("No final 12.0.3 governed rows available.")

        STATE["rows_total"] = len(rows)
        STATE["phase"] = "CLASSIFYING_PROMOTION_EVIDENCE"

        counts = {}
        with e.begin() as c:
            for r in rows:
                status, reasons, blockers = _classify(r)
                counts[status] = counts.get(status, 0) + 1

                c.execute(text(f"""
                    INSERT INTO {GATE}(
                      source_id,gate_status,evidence_rule,evidence_confidence,
                      canonical_location,quality_status,property_status,source_conflict,
                      certification_status,duplicate_group,duplicate_decision,
                      reasons,blockers,version,updated_at
                    )
                    VALUES(
                      :sid,:gs,:rule,:conf,:loc,:qs,:ps,:sc,:cs,:dg,:dd,
                      CAST(:reasons AS JSONB),CAST(:blockers AS JSONB),:ver,NOW()
                    )
                    ON CONFLICT(source_id) DO UPDATE SET
                      gate_status=EXCLUDED.gate_status,
                      evidence_rule=EXCLUDED.evidence_rule,
                      evidence_confidence=EXCLUDED.evidence_confidence,
                      canonical_location=EXCLUDED.canonical_location,
                      quality_status=EXCLUDED.quality_status,
                      property_status=EXCLUDED.property_status,
                      source_conflict=EXCLUDED.source_conflict,
                      certification_status=EXCLUDED.certification_status,
                      duplicate_group=EXCLUDED.duplicate_group,
                      duplicate_decision=EXCLUDED.duplicate_decision,
                      reasons=EXCLUDED.reasons,
                      blockers=EXCLUDED.blockers,
                      version=EXCLUDED.version,
                      updated_at=NOW()
                """), {
                    "sid": r["source_id"],
                    "gs": status,
                    "rule": r.get("location_rule"),
                    "conf": int(r.get("location_confidence") or 0),
                    "loc": r.get("canonical_location"),
                    "qs": r.get("quality_status"),
                    "ps": r.get("property_status"),
                    "sc": bool(r.get("conflict")),
                    "cs": r.get("cert_decision"),
                    "dg": r.get("duplicate_group"),
                    "dd": r.get("duplicate_decision"),
                    "reasons": json.dumps(reasons, ensure_ascii=False),
                    "blockers": json.dumps(blockers, ensure_ascii=False),
                    "ver": VERSION,
                })

        STATE.update({
            "status":"PASS",
            "phase":"COMPLETE",
            "completed_at":_now(),
            "rows_scored":len(rows),
            "proven_already_certified":counts.get("PROVEN_ALREADY_CERTIFIED",0),
            "proven_blocked_duplicate":counts.get("PROVEN_BLOCKED_DUPLICATE",0),
            "proven_blocked_conflict":counts.get("PROVEN_BLOCKED_CONFLICT",0),
            "proven_candidate":counts.get("PROVEN_CANDIDATE",0),
            "silver_not_proven":counts.get("SILVER_NOT_PROVEN",0),
            "needs_evidence":counts.get("NEEDS_EVIDENCE",0),
            "excluded_non_property":counts.get("EXCLUDED_NON_PROPERTY",0),
            "details":{
                "raw_master_mutation":"NONE",
                "certification_mutation":"NONE",
                "duplicate_mutation":"NONE",
                "promotion_policy":"CANDIDATE_ONLY",
                "proven_rules":sorted(PROVEN_RULES),
                "minimum_proven_confidence":93,
                "existing_valid_geography_is_not_certification_evidence":True,
                "bulk_promotion_enabled":False,
                "dependency_guard":True,
                "final_stage_readiness":readiness,
                "gate_table":GATE
            }
        })

        if run_id:
            with e.begin() as c:
                c.execute(text(f"""
                    UPDATE {RUNS}
                    SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB)
                    WHERE id=:id
                """), {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False,default=str)})

    except Exception as exc:
        STATE.update({
            "status":"ERROR","phase":"FAILED","completed_at":_now(),
            "error":f"{type(exc).__name__}: {exc}",
            "details":{
                "trace":traceback.format_exc()[-7000:],
                "raw_master_mutation":"NONE",
                "certification_mutation":"NONE",
                "duplicate_mutation":"NONE"
            }
        })
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"""
                        UPDATE {RUNS}
                        SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB)
                        WHERE id=:id
                    """), {"id":run_id,"s":json.dumps(STATE,default=str)})
            except Exception:
                pass
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k":LOCK_KEY})
            except Exception:
                pass
            try:
                lock_conn.close()
            except Exception:
                pass

def _start(core):
    threading.Thread(
        target=_build,
        args=(core,),
        daemon=True,
        name="mag-evidence-promotion-gate-120062"
    ).start()

def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("12.0.6.2 requires app + engine")

    _setup(e)

    @app.get("/api/alliance/admin/magazine-promotion-gate/status")
    def promotion_gate_status():
        return JSONResponse(STATE)

    @app.post("/api/alliance/admin/magazine-promotion-gate/rebuild")
    def promotion_gate_rebuild():
        _start(core)
        return JSONResponse({"status":"STARTED","version":VERSION})

    @app.get("/alliance/admin/magazine-promotion-gate", response_class=HTMLResponse)
    def promotion_gate_page(req: Request, bucket: str="PROVEN_CANDIDATE", page: int=1):
        _login(core, req)
        allowed = {
            "PROVEN_CANDIDATE","PROVEN_BLOCKED_DUPLICATE","PROVEN_BLOCKED_CONFLICT",
            "SILVER_NOT_PROVEN","NEEDS_EVIDENCE","PROVEN_ALREADY_CERTIFIED",
            "EXCLUDED_NON_PROPERTY"
        }
        bucket = str(bucket or "PROVEN_CANDIDATE").upper()
        if bucket not in allowed:
            bucket = "PROVEN_CANDIDATE"
        page = max(1, int(page or 1))
        per = 40
        off = (page - 1) * per

        with e.connect() as c:
            counts = {
                r[0]: int(r[1])
                for r in c.execute(text(f"""
                    SELECT gate_status,COUNT(*)
                    FROM {GATE}
                    GROUP BY gate_status
                """)).all()
            }
            rows = c.execute(text(f"""
                SELECT
                  g.source_id,g.gate_status,g.evidence_rule,g.evidence_confidence,
                  g.canonical_location,g.quality_status,g.property_status,
                  g.source_conflict,g.certification_status,g.duplicate_group,
                  g.duplicate_decision,g.reasons,g.blockers,
                  m.original_raw_text
                FROM {GATE} g
                JOIN pi_magazine_master m
                  ON CAST(m.source_id AS TEXT)=g.source_id
                WHERE g.gate_status=:bucket
                ORDER BY g.evidence_confidence DESC,g.source_id
                LIMIT :lim OFFSET :off
            """), {"bucket":bucket,"lim":per,"off":off}).mappings().all()

        cards = []
        for r in rows:
            reasons = r["reasons"] if isinstance(r["reasons"], list) else []
            blockers = r["blockers"] if isinstance(r["blockers"], list) else []
            cards.append(f"""
              <div class="card">
                <b>{html.escape(str(r['source_id']))}</b> ·
                {html.escape(str(r['gate_status']))} ·
                evidence {html.escape(str(r['evidence_rule'] or 'NONE'))}
                ({r['evidence_confidence'] or 0})
                <br><b>Location:</b> {html.escape(str(r['canonical_location'] or 'MISSING'))}
                <br><b>Description:</b> {html.escape(str(r['original_raw_text'] or ''))}
                <br><b>Reasons:</b> {html.escape('; '.join(map(str,reasons)) or '—')}
                <br><b>Blockers:</b> {html.escape('; '.join(map(str,blockers)) or '—')}
                <br><b>Duplicate:</b> {html.escape(str(r['duplicate_group'] or 'none'))} ·
                {html.escape(str(r['duplicate_decision'] or ''))}
              </div>
            """)

        def n(k): return counts.get(k, 0)

        return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
        <title>Magazine Evidence Promotion Gate</title>
        <style>
        body{{font-family:Arial;margin:24px;background:#f5f2eb;color:#28231f}}
        .card,.top{{background:white;padding:14px;border:1px solid #ddd;border-radius:10px;margin:10px 0;line-height:1.5}}
        a{{margin-right:12px}}
        </style></head><body>
        <h1>Magazine Evidence-Proven Promotion Gate · 12.0.6.2</h1>
        <div class="top">
          <b>No automatic promotion.</b> This layer only identifies records whose locality is independently
          proven by direct description, exact layout evidence, or exact complete-source evidence.<br><br>
          Proven Candidate <b>{n('PROVEN_CANDIDATE')}</b> ·
          Proven/Already Certified <b>{n('PROVEN_ALREADY_CERTIFIED')}</b> ·
          Blocked Duplicate <b>{n('PROVEN_BLOCKED_DUPLICATE')}</b> ·
          Blocked Conflict <b>{n('PROVEN_BLOCKED_CONFLICT')}</b> ·
          Silver Not Proven <b>{n('SILVER_NOT_PROVEN')}</b> ·
          Needs Evidence <b>{n('NEEDS_EVIDENCE')}</b> ·
          Excluded <b>{n('EXCLUDED_NON_PROPERTY')}</b>
        </div>
        <div class="top">
          <a href="?bucket=PROVEN_CANDIDATE">Proven Candidates</a>
          <a href="?bucket=PROVEN_BLOCKED_DUPLICATE">Blocked Duplicates</a>
          <a href="?bucket=PROVEN_BLOCKED_CONFLICT">Blocked Conflicts</a>
          <a href="?bucket=SILVER_NOT_PROVEN">Silver Not Proven</a>
          <a href="?bucket=NEEDS_EVIDENCE">Needs Evidence</a>
          <a href="?bucket=PROVEN_ALREADY_CERTIFIED">Already Certified</a>
          <a href="/api/alliance/admin/magazine-promotion-gate/status">Status JSON</a>
        </div>
        {''.join(cards) if cards else '<div class="card">No records in this bucket.</div>'}
        <p><a href="?bucket={bucket}&page={page+1}">Next page →</a></p>
        </body></html>""")

    _start(core)
    return {
        "status":"REGISTERED",
        "version":VERSION,
        "policy":"CANDIDATE_ONLY_NO_AUTOPROMOTION",
        "dashboard":"/alliance/admin/magazine-promotion-gate",
        "status_api":"/api/alliance/admin/magazine-promotion-gate/status"
    }
