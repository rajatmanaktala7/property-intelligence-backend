from __future__ import annotations

import html
import threading
from datetime import datetime, timezone
from fastapi import Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

VERSION = "12.4.22-HOSPITALITY-CONTACT-DISCOVERY-BOT"

_LOCK = threading.Lock()
_JOB = {
    "running": False,
    "requested": 0,
    "processed": 0,
    "high": 0,
    "verify": 0,
    "low": 0,
    "none": 0,
    "errors": 0,
    "started_at": None,
    "completed_at": None,
    "last_message": "Ready",
}

def _app(core):
    return getattr(core, "app", None) or core

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _utc():
    return datetime.now(timezone.utc).isoformat()

def _e(v):
    return html.escape("" if v is None else str(v), quote=True)

def _remove_get(app, path):
    kept=[]
    removed=0
    for r in list(app.router.routes):
        methods=set(getattr(r,"methods",set()) or set())
        if getattr(r,"path",None)==path and "GET" in methods:
            removed += 1
        else:
            kept.append(r)
    app.router.routes[:] = kept
    return removed

def _snapshot(engine):
    import alliance_hospitality_phone_recovery_v12417 as recovery
    st = recovery.stats(engine)
    return {
        "active": int(st.get("active") or 0),
        "phones_saved": int(st.get("phones_saved") or 0),
        "unique_processed": int(st.get("unique_processed") or 0),
        "pending": int(st.get("pending") or 0),
        "high": int(st.get("high_confidence_candidates") or 0),
        "verify": int(st.get("needs_verification_candidates") or 0),
        "low": int(st.get("low_confidence_candidates") or 0),
        "no_phone": int(st.get("no_phone_found") or 0),
        "errors": int(st.get("errors") or 0),
    }

def _worker(engine, requested):
    global _JOB
    processed=high=verify=low=none=0
    try:
        import alliance_hospitality_phone_recovery_v12417 as recovery
        remaining = int(requested)
        while remaining > 0:
            take = min(5, remaining)
            out = recovery.run_batch(engine, take)
            got = int(out.get("unique_processed") or 0)
            if got <= 0:
                break
            processed += got
            high += int(out.get("high_confidence_saved") or 0)
            verify += int(out.get("needs_verification") or 0)
            low += int(out.get("low_confidence") or 0)
            none += int(out.get("no_phone_found") or 0)
            remaining -= got
            with _LOCK:
                _JOB.update({
                    "processed": processed,
                    "high": high,
                    "verify": verify,
                    "low": low,
                    "none": none,
                    "last_message": f"Running: {processed}/{requested} processed",
                })
            if got < take:
                break
        with _LOCK:
            _JOB.update({
                "running": False,
                "processed": processed,
                "high": high,
                "verify": verify,
                "low": low,
                "none": none,
                "completed_at": _utc(),
                "last_message": f"Completed: {processed} processed · high {high} · verify {verify} · low {low} · no phone {none}",
            })
    except Exception as exc:
        with _LOCK:
            _JOB.update({
                "running": False,
                "errors": int(_JOB.get("errors") or 0) + 1,
                "completed_at": _utc(),
                "last_message": f"ERROR: {type(exc).__name__}: {exc}",
            })

def _start(engine, requested):
    requested=max(1,min(int(requested),5000))
    with _LOCK:
        if _JOB["running"]:
            return False, "A contact discovery job is already running."
        _JOB.update({
            "running": True,
            "requested": requested,
            "processed": 0,
            "high": 0,
            "verify": 0,
            "low": 0,
            "none": 0,
            "errors": 0,
            "started_at": _utc(),
            "completed_at": None,
            "last_message": f"Started contact discovery for next {requested}",
        })
    t=threading.Thread(target=_worker,args=(engine,requested),daemon=True,name="hospitality-contact-bot-v12422")
    t.start()
    return True, f"Started next {requested} contact discovery."

def _page(core, req, msg=""):
    _login(core, req)
    st=_snapshot(core.engine)
    with _LOCK:
        job=dict(_JOB)

    status = "RUNNING" if job["running"] else "READY"
    body=f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hospitality Contact Discovery Bot</title>
<style>
*{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}}
.wrap{{max-width:1500px;margin:auto;padding:18px}}.top,.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:16px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}.m{{background:#f8fafc;border:1px solid #e4e7ec;border-radius:10px;padding:12px}}.m strong{{font-size:26px;display:block}}
.btn,button{{background:#102a43;color:#fff;border:0;border-radius:8px;padding:10px 12px;text-decoration:none;cursor:pointer;font-weight:700;margin:4px}}
.good{{background:#067647}}.warn{{background:#b54708}}.muted{{color:#667085}}.note{{background:#fff7e6;border:1px solid #f3cf8c;padding:12px;border-radius:9px}}
.ok{{background:#ecfdf3;border:1px solid #abefc6;color:#067647;padding:10px;border-radius:8px}}
</style></head><body><div class="wrap">
<div class="top">
<h1>Hospitality Contact Discovery Bot · 12.4.22</h1>
<p class="muted">Dedicated bot for fetching more hospitality contacts. The master Hospitality Intelligence database remains separate and unchanged.</p>
<p><a class="btn" href="/hospitality-intelligence">Open Hospitality Intelligence</a> <a class="btn" href="/alliance/primary">Back to Dashboard</a></p>
</div>

{f'<div class="ok">{_e(msg)}</div>' if msg else ''}

<div class="card"><h2>Contact Coverage</h2><div class="grid">
<div class="m"><span>Active Businesses</span><strong>{st["active"]:,}</strong></div>
<div class="m"><span>Phones Saved</span><strong>{st["phones_saved"]:,}</strong></div>
<div class="m"><span>Processed</span><strong>{st["unique_processed"]:,}</strong></div>
<div class="m"><span>Still Missing / Pending</span><strong>{st["pending"]:,}</strong></div>
<div class="m"><span>High Confidence</span><strong>{st["high"]:,}</strong></div>
<div class="m"><span>Needs Verification</span><strong>{st["verify"]:,}</strong></div>
<div class="m"><span>Low Confidence</span><strong>{st["low"]:,}</strong></div>
<div class="m"><span>Errors</span><strong>{st["errors"]:,}</strong></div>
</div></div>

<div class="card"><h2>Fetch More Contacts</h2>
<p class="muted">Uses the proven 12.4.20B phone-recovery engine in safe 5-business internal chunks. Existing phone values are never overwritten. Only HIGH_CONFIDENCE numbers are auto-saved; one-source candidates remain for verification.</p>
<form method="post" action="/v3/hospitality-intelligence/run?limit=5" style="display:inline"><button>Test Next 5</button></form>
<form method="post" action="/v3/hospitality-intelligence/run?limit=25" style="display:inline"><button class="good">Fetch Next 25</button></form>
<form method="post" action="/v3/hospitality-intelligence/run?limit=100" style="display:inline"><button class="good">Fetch Next 100</button></form>
<form method="post" action="/v3/hospitality-intelligence/run?limit=500" style="display:inline"><button class="warn">Fetch Next 500</button></form>
</div>

<div class="card"><h2>Current Bot Job</h2>
<div class="grid">
<div class="m"><span>Status</span><strong>{status}</strong></div>
<div class="m"><span>Requested</span><strong>{int(job["requested"] or 0)}</strong></div>
<div class="m"><span>Processed This Job</span><strong>{int(job["processed"] or 0)}</strong></div>
<div class="m"><span>High This Job</span><strong>{int(job["high"] or 0)}</strong></div>
<div class="m"><span>Verify This Job</span><strong>{int(job["verify"] or 0)}</strong></div>
<div class="m"><span>No Phone This Job</span><strong>{int(job["none"] or 0)}</strong></div>
</div>
<p><b>{_e(job["last_message"])}</b></p>
<p class="muted">Started: {_e(job["started_at"])} · Completed: {_e(job["completed_at"])}</p>
<p><a class="btn" href="/v3/hospitality-intelligence">Refresh Status</a></p>
</div>

<div class="note"><b>Safety:</b> This bot does not create a second hospitality database, does not run the unsafe name-only dedupe, does not overwrite existing phone numbers, and does not change matcher/property/requirement data.</div>
</div></body></html>'''
    return HTMLResponse(body,headers={"Cache-Control":"no-store"})

def register(core):
    app=_app(core)
    if app is None or getattr(core,"engine",None) is None:
        raise RuntimeError("12.4.22 requires app + engine")

    # Keep /hospitality-intelligence as the master database page.
    # Replace ONLY the duplicate bot alias with the dedicated contact bot.
    removed=_remove_get(app,"/v3/hospitality-intelligence")

    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def hospitality_contact_bot(req:Request,msg:str=Query("")):
        return _page(core,req,msg)

    @app.post("/v3/hospitality-intelligence/run")
    def hospitality_contact_run(req:Request,limit:int=Query(25)):
        _login(core,req)
        ok,msg=_start(core.engine,limit)
        return RedirectResponse("/v3/hospitality-intelligence?msg="+msg.replace(" ","+"),303)

    @app.get("/api/alliance/hospitality-contact-bot/status")
    def hospitality_contact_status(req:Request):
        _login(core,req)
        with _LOCK:
            job=dict(_JOB)
        return {
            "status":"PASS",
            "version":VERSION,
            "master_database_route":"/hospitality-intelligence",
            "contact_bot_route":"/v3/hospitality-intelligence",
            "duplicate_database_removed":True,
            "uses_recovery_engine":"12.4.20B",
            "existing_phones_preserved":True,
            "unsafe_name_only_dedupe_used":False,
            "job":job,
            "coverage":_snapshot(core.engine),
        }

    return {
        "status":"AUTHORITATIVE",
        "version":VERSION,
        "removed_duplicate_bot_get_routes":removed,
        "master_database_unchanged":True,
        "contact_bot_route":"/v3/hospitality-intelligence",
        "background_batches":True,
    }
