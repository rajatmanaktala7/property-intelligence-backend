import app as core
from datetime import datetime, timezone
from fastapi.responses import HTMLResponse

app = core.app

try:
    from alliance_v2_routes import register as register_alliance_v2
    register_alliance_v2(core)
    ALLIANCE_V2_STATUS={"status":"HEALTHY","error":None}
except Exception as e:
    ALLIANCE_V2_STATUS={"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}

try:
    import alliance_module_registry as registry
    OPTIONAL_MODULES=registry.register_all(core)
except Exception as e:
    OPTIONAL_MODULES={"registry":{"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}}

@app.get("/production-health")
def production_health():
    return {
        "status":"OK",
        "service":"Alliance Property Intelligence",
        "wrapper":"V3.8-STABLE-CONSOLIDATED",
        "core_app_loaded":True,
        "alliance_v2":ALLIANCE_V2_STATUS,
        "optional_modules":OPTIONAL_MODULES,
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
    }

@app.get("/module-health")
def module_health():
    return {"wrapper":"V3.8-STABLE-CONSOLIDATED","alliance_v2":ALLIANCE_V2_STATUS,"modules":OPTIONAL_MODULES}

try:
    import alliance_v44_whatsapp_property_master as _v44
    _v44.register(core)
    import alliance_auto_updater as _auto44
    _auto44.start(core)

    @app.get("/api/v44/auto-update/status")
    def _v44_auto_status():
        return _auto44.STATE
except Exception as e:
    print("Alliance V4.4 registration warning:", type(e).__name__, str(e))

V383_ERROR=None
try:
    import alliance_v383_database_foundation as _v383
    _v383.register(core)
except Exception as e:
    V383_ERROR=f"{type(e).__name__}: {e}"

V46_ERROR=None
try:
    import alliance_v46_unified_intelligence as _v46
    _v46.register(core)
except Exception as e:
    V46_ERROR=f"{type(e).__name__}: {e}"

V451_ERROR=None
try:
    import alliance_v45_live_whatsapp_takeover as _v451
    _v451.register(core)
except Exception as e:
    V451_ERROR=f"{type(e).__name__}: {e}"

@app.middleware("http")
async def force_clean_whatsapp_live_feed(request, call_next):
    if request.method=="GET" and request.url.path=="/whatsapp-live/feed":
        if V451_ERROR is not None:
            return HTMLResponse(f"<h2>Clean feed unavailable</h2><p>{V451_ERROR}</p>",status_code=503)
        try:
            try:
                _auto44.request_refresh(force=False)
            except Exception:
                pass
            sync=_v451._sync_latest_whatsapp_to_canonical(core.engine,5000)
            q=str(request.query_params.get("q") or "").strip()
            rows=_v451._canonical_rows(core.engine,q,1200)

            trs="".join(
                "<tr>"
                f"<td>{_v451._esc(r.get('record_id'))}</td>"
                f"<td>{_v451._esc(r.get('lead_type'))}</td>"
                f"<td class='desc'>{_v451._esc(r.get('description'))}</td>"
                f"<td>{_v451._esc(r.get('area'))}</td>"
                f"<td>{_v451._esc(r.get('configuration_details'))}</td>"
                f"<td>{_v451._esc(r.get('price'))}</td>"
                f"<td>{_v451._esc(r.get('contact_name_number'))}</td>"
                f"<td>{_v451._esc(r.get('source'))}</td>"
                f"<td>{_v451._esc(r.get('captured_on'))}</td>"
                f"<td>{_v451._esc(r.get('verification'))}</td>"
                f"<td>{_v451._esc(r.get('source_count'))}</td>"
                "</tr>" for r in rows
            )

            body=f"""
            <h2>WhatsApp Property Availability</h2>
            <div class='card'>
              <form method='get' action='/whatsapp-live/feed' style='display:grid;grid-template-columns:1fr auto;gap:8px'>
                <input name='q' value='{_v451._esc(q)}' placeholder='Search location, property type, rent/sale, project, contact or group'>
                <button>Search</button>
              </form>
              <p class='muted'>Clean canonical property inventory only. Rejected messages, greetings, requirements, contacts-only posts and review/noise records are hidden.</p>
              <p><b>Canonical sync:</b> {_v451._esc(sync.get('status'))} · processed {_v451._esc(sync.get('processed',0))} · <b>properties shown:</b> {len(rows)}</p>
            </div>
            <div class='card' style='overflow:auto'>
              <table>
                <tr><th>Record ID</th><th>Rent / Sale</th><th>Description</th><th>Area</th><th>Property / Location</th><th>Price / Rent</th><th>Contact Name / Number</th><th>Source Group</th><th>Captured On</th><th>Verification</th><th>Sources Merged</th></tr>
                {trs or '<tr><td colspan="11">No clean property records found.</td></tr>'}
              </table>
            </div>
            """
            return HTMLResponse(_v451._page("WhatsApp Property Availability",body))
        except Exception as exc:
            return HTMLResponse(
                _v451._page("WhatsApp Property Availability",
                f"<div class='card'><h2>Clean feed error</h2><p>{_v451._esc(type(exc).__name__)}: {_v451._esc(exc)}</p></div>"),
                status_code=500
            )
    return await call_next(request)

@app.get("/api/live-bootstrap-status")
def live_bootstrap_status():
    paths=[]
    for r in app.router.routes:
        p=getattr(r,"path",None)
        if p in {"/whatsapp-live","/whatsapp-live/feed","/api/v451/live/status","/api/v451/live/properties","/api/v383/status","/api/v46/status"}:
            paths.append(p)
    return {
        "status":"OK" if V451_ERROR is None else "DEGRADED",
        "v383_error":V383_ERROR,
        "v46_error":V46_ERROR,
        "v451_error":V451_ERROR,
        "registered_paths":sorted(set(paths)),
        "live_feed_owner":"FORCED_CANONICAL_MIDDLEWARE",
        "live_feed_reads_raw_bridge_events":False,
        "rejected_visible_in_live_feed":False,
    }
